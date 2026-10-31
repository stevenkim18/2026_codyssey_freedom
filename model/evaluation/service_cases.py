"""Validate the independent service test set (B4): counts, offsets, coverage, duplicates.

Validation never marks a set as frozen: ``status`` is ``draft_needs_review`` while
any document is not human-``reviewed``; ``ready_to_freeze`` only means every
mechanical check passed and every document was reviewed. Freezing is a separate,
explicit human act recorded outside this tool.

    python -m model.evaluation.service_cases --dataset model/evaluation/service_testset/service-test-v1.draft.jsonl \\
        --official-root model/.cache/k-pii-bench/<revision>/data --manifest model/evaluation/service-test-v1.manifest.json
"""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import platform
import re
import sys

VALIDATOR_VERSION = 'service-cases-validator-v1'
TYPES = ('KR_RRN', 'CREDIT_CARD', 'PERSON', 'EMAIL_ADDRESS', 'KR_PHONE_NUMBER', 'KR_BANK_ACCOUNT', 'KR_LOCATION',
         'DATE_OF_BIRTH', 'KR_DRIVER_LICENSE', 'KR_PASSPORT', 'KR_NHIS', 'KR_VEHICLE_PLATE', 'USERNAME',
         'PERSONAL_ATTRIBUTE', 'ORGANIZATION', 'URL', 'IP_ADDRESS', 'NRP')
CRITICAL_TYPES = ('KR_RRN', 'KR_BANK_ACCOUNT', 'CREDIT_CARD', 'KR_PHONE_NUMBER', 'EMAIL_ADDRESS')
REQUIRED = {'documents': 300, 'request': 150, 'response': 150, 'min_normal': 100, 'min_critical_entities': 30}
REQUIRED_TAGS = ('table', 'json', 'code', 'particle', 'long', 'mixed_language', 'boundary')
# Types with no gold support in official dev/test; the service set must exercise them.
MISSING_OFFICIAL_TYPES = ('PERSONAL_ATTRIBUTE',)
MIN_MISSING_TYPE_ENTITIES = 10
MIN_LONG_CHARS = 600
MIN_LONG_DOCUMENTS = 5
MAX_PER_SKELETON = 4
MIN_DISTINCT_SKELETONS = 80
MIN_SURFACE_LEN_FOR_LEAK = 8


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    rows = []
    with Path(path).open('r', encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                raise ValueError(f'invalid_json_line:{number}') from None
    return rows


def skeleton(row):
    text, pieces, last = row['text'], [], 0
    for e in sorted(row['entities'], key=lambda e: (e['start'], e['end'])):
        if e['start'] >= last:
            pieces.append(text[last:e['start']])
            pieces.append('<' + e['type'] + '>')
            last = e['end']
    pieces.append(text[last:])
    return re.sub(r'\d', '0', ''.join(pieces))


def official_index(root):
    """Text hashes and long entity surfaces of the official splits (streamed, never stored raw to disk)."""
    texts, surfaces = set(), set()
    for name in ('train', 'dev', 'test_track_a'):
        path = Path(root) / f'{name}.jsonl'
        if not path.is_file():
            raise FileNotFoundError(f'official_split_missing:{name}')
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                row = json.loads(line)
                texts.add(hashlib.sha256(row['text'].encode()).hexdigest())
                surfaces.update(e['surface'] for e in row['entities'] if len(e['surface']) >= MIN_SURFACE_LEN_FOR_LEAK)
    return texts, surfaces


def validate(rows, official=None):
    errors, warnings = [], []
    ids, texts = set(), Counter()
    by_type, by_direction, tags = Counter(), Counter(), Counter()
    normal = long_docs = 0
    skeletons = Counter()
    reviewed = Counter()
    entity_docs = 0
    for row in rows:
        rid = row.get('id') if isinstance(row, dict) else None
        if (not isinstance(row, dict) or not isinstance(rid, str) or not rid or rid in ids
                or not isinstance(row.get('text'), str) or not row['text'].strip()
                or row.get('direction') not in ('request', 'response') or not isinstance(row.get('entities'), list)
                or not isinstance(row.get('tags'), list) or not isinstance(row.get('generation'), dict)):
            errors.append(f'invalid_row:{rid}')
            continue
        ids.add(rid)
        if '⟦' in row['text'] or '⟧' in row['text']:
            errors.append(f'unrendered_placeholder:{rid}')
        by_direction[row['direction']] += 1
        texts[row['text']] += 1
        reviewed[row.get('review_status')] += 1
        tags.update(row['tags'])
        spans = set()
        for e in row['entities']:
            a, b = e.get('start') if isinstance(e, dict) else None, e.get('end') if isinstance(e, dict) else None
            if (not isinstance(e, dict) or e.get('type') not in TYPES or type(a) is not int or type(b) is not int
                    or not 0 <= a < b <= len(row['text']) or row['text'][a:b] != e.get('surface')):
                errors.append(f'invalid_entity:{rid}')
                continue
            key = (e['type'], a, b)
            if key in spans:
                errors.append(f'duplicate_entity:{rid}')
            spans.add(key)
            by_type[e['type']] += 1
        if row['entities']:
            entity_docs += 1
        else:
            normal += 1
        if len(row['text']) >= MIN_LONG_CHARS:
            long_docs += 1
        skeletons[skeleton(row)] += 1
    expected = REQUIRED
    if len(rows) != expected['documents']:
        errors.append(f'document_count:{len(rows)}!={expected["documents"]}')
    for direction in ('request', 'response'):
        if by_direction[direction] != expected[direction]:
            errors.append(f'{direction}_count:{by_direction[direction]}!={expected[direction]}')
    if normal < expected['min_normal']:
        errors.append(f'normal_documents:{normal}<{expected["min_normal"]}')
    for kind in CRITICAL_TYPES:
        if by_type[kind] < expected['min_critical_entities']:
            errors.append(f'critical_entities:{kind}:{by_type[kind]}<{expected["min_critical_entities"]}')
    for tag in REQUIRED_TAGS:
        if not tags[tag]:
            errors.append(f'missing_tag:{tag}')
    for kind in MISSING_OFFICIAL_TYPES:
        if by_type[kind] < MIN_MISSING_TYPE_ENTITIES:
            errors.append(f'missing_official_type_entities:{kind}:{by_type[kind]}<{MIN_MISSING_TYPE_ENTITIES}')
    for kind in TYPES:
        if not by_type[kind]:
            errors.append(f'type_without_gold:{kind}')
    if long_docs < MIN_LONG_DOCUMENTS:
        errors.append(f'long_documents:{long_docs}<{MIN_LONG_DOCUMENTS}')
    repeated = [t for t, n in texts.items() if n > 1]
    if repeated:
        errors.append(f'duplicate_texts:{len(repeated)}')
    if len(skeletons) < MIN_DISTINCT_SKELETONS:
        errors.append(f'distinct_skeletons:{len(skeletons)}<{MIN_DISTINCT_SKELETONS}')
    worst = max(skeletons.values(), default=0)
    if worst > MAX_PER_SKELETON:
        errors.append(f'skeleton_repeated:{worst}>{MAX_PER_SKELETON}')
    overlap = {'exact_text': None, 'long_surface': None}
    if official is None:
        warnings.append('official_overlap_not_checked')
    else:
        official_texts, official_surfaces = official
        overlap['exact_text'] = sum(1 for r in rows if hashlib.sha256(r['text'].encode()).hexdigest() in official_texts)
        overlap['long_surface'] = sorted({e['type'] for r in rows for e in r['entities']
                                          if e['surface'] in official_surfaces})
        overlap['long_surface_entities'] = sum(1 for r in rows for e in r['entities'] if e['surface'] in official_surfaces)
        if overlap['exact_text']:
            errors.append(f'official_text_overlap:{overlap["exact_text"]}')
        if overlap['long_surface_entities']:
            errors.append(f'official_surface_overlap:{overlap["long_surface_entities"]}')
    unreviewed = len(rows) - reviewed['reviewed']
    status = 'invalid' if errors else ('draft_needs_review' if unreviewed else 'ready_to_freeze')
    return {'validator_version': VALIDATOR_VERSION, 'status': status, 'errors': errors, 'warnings': warnings,
            'counts': {'documents': len(rows), 'by_direction': dict(by_direction), 'normal_documents': normal,
                       'documents_with_gold': entity_docs, 'entities_by_type': {k: by_type[k] for k in TYPES},
                       'tags': dict(sorted(tags.items())), 'long_documents_ge_%d_chars' % MIN_LONG_CHARS: long_docs,
                       'distinct_skeletons': len(skeletons), 'max_instances_per_skeleton': worst,
                       'review_status': dict(reviewed), 'unreviewed_documents': unreviewed},
            'official_overlap': overlap, 'frozen': False}


def manifest(dataset_path, report, builder_path=None, regeneration=None):
    return {'dataset_version': 'service-test-v1-draft', 'file': Path(dataset_path).name,
            'sha256': _sha256(dataset_path), 'bytes': Path(dataset_path).stat().st_size,
            'builder_sha256': None if builder_path is None else _sha256(builder_path),
            'validator_version': VALIDATOR_VERSION, 'python': platform.python_version(),
            'validation': report, 'regeneration': regeneration,
            'git_policy': 'JSONL is Git-ignored (*.jsonl). Commit this manifest and the builder; regenerate the file deterministically and compare sha256.',
            'frozen': False,
            'freeze_requirements': ['human review of every document (review_status=reviewed)',
                                    'status ready_to_freeze with official overlap checked',
                                    'sha256 recorded in the experiment record before any M0/M1 run'],
            'use_policy': 'final validation only; never for dev tuning or threshold selection'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--official-root', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args(argv)
    rows = load(args.dataset)
    official = official_index(args.official_root) if args.official_root else None
    report = validate(rows, official)
    if args.manifest:
        builder = Path(__file__).with_name('service_testset_builder.py')
        text = json.dumps(manifest(args.dataset, report, builder, 'python -m model.evaluation.service_testset_builder --output-dir <dir>'),
                          ensure_ascii=False, indent=2)
        with args.manifest.open('x', encoding='utf-8') as handle:
            handle.write(text + '\n')
    print(json.dumps({'status': report['status'], 'errors': report['errors'], 'warnings': report['warnings']}, ensure_ascii=False))
    return 0 if report['status'] != 'invalid' else 1


if __name__ == '__main__':
    sys.exit(main())
