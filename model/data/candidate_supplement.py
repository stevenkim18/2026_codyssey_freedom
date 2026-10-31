"""Task C: verify and size the candidate supplement ``townboy/korean-pii-dataset``.

This is a *candidate check*, not adoption. K-PII-Bench stays the PRD data. The
supplement is never merged into the original files; its own splits are kept, its
``test`` is never used for training, and no gold is deleted to make a document
convertible: a document with a label that has no confirmed/tentative project type
is rejected whole (reason recorded).

Gold comes only from the raw character spans (``privacy_mask``), re-verified by
``source_text[start:end] == value``. The candidate's BIO configuration (other
model's tokenization) is not read.
"""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket

from .manifest import MODEL_ROOT, strict_json

CANDIDATE_REPO = 'townboy/korean-pii-dataset'
CANDIDATE_REVISION = '076419f07f7073b6b28cdfdb6fbd8b75e1278053'
LOCK_PATH = MODEL_ROOT / 'configs/candidate-townboy-lock.json'
SPLIT_FILES = {'train': 'data/train.jsonl', 'validation': 'data/validation.jsonl', 'test': 'data/test.jsonl'}
MIN_SURFACE_LEN_FOR_LEAK = 8

# 33 candidate labels -> 18 project types. status: confirmed | tentative | unmapped.
# tentative = plausible but semantics differ or are undecided (needs A/B agreement).
LABEL_MAP = {
    'NAME': ('PERSON', 'confirmed', ''),
    'EMAIL': ('EMAIL_ADDRESS', 'confirmed', ''),
    'PHONE': ('KR_PHONE_NUMBER', 'confirmed', 'region KR only is stated; foreign formats unverified'),
    'ACCOUNT_NUMBER': ('KR_BANK_ACCOUNT', 'confirmed', ''),
    'CARD_NUMBER': ('CREDIT_CARD', 'confirmed', ''),
    'RRN': ('KR_RRN', 'confirmed', ''),
    'ADDRESS': ('KR_LOCATION', 'confirmed', 'full street address'),
    'BIRTHDATE': ('DATE_OF_BIRTH', 'confirmed', ''),
    'DRIVER_LICENSE': ('KR_DRIVER_LICENSE', 'confirmed', ''),
    'PASSPORTNUM': ('KR_PASSPORT', 'confirmed', ''),
    'VEHICLE_NUMBER': ('KR_VEHICLE_PLATE', 'confirmed', ''),
    'IP_ADDRESS': ('IP_ADDRESS', 'confirmed', ''),
    'URL': ('URL', 'confirmed', ''),
    'NATIONALITY': ('NRP', 'confirmed', 'nationality part of NRP'),
    'RELIGION': ('NRP', 'confirmed', 'religion part of NRP'),
    'CITY': ('KR_LOCATION', 'tentative', 'city-level fragment; K-PII-Bench KR_LOCATION granularity not verified'),
    'USER_ID': ('USERNAME', 'tentative', 'login id vs K-PII-Bench USERNAME semantics not verified'),
    'NICKNAME': ('USERNAME', 'tentative', 'nickname is not necessarily an account name'),
    'WORKPLACE': ('ORGANIZATION', 'tentative', 'employer vs generic organization'),
    'SCHOOL': ('ORGANIZATION', 'tentative', 'school is an organization but may be personal attribute'),
    'DEPARTMENT': ('ORGANIZATION', 'tentative', 'internal unit; policy ALLOW for ORGANIZATION may hide risk'),
    'BLOOD_TYPE': ('PERSONAL_ATTRIBUTE', 'tentative', 'PERSONAL_ATTRIBUTE definition in K-PII-Bench unverified'),
    'HEIGHT': ('PERSONAL_ATTRIBUTE', 'tentative', 'same'),
    'WEIGHT': ('PERSONAL_ATTRIBUTE', 'tentative', 'same'),
    'AGE': ('PERSONAL_ATTRIBUTE', 'tentative', 'same'),
    'GENDER': ('PERSONAL_ATTRIBUTE', 'tentative', 'same'),
    'MAJOR': ('PERSONAL_ATTRIBUTE', 'tentative', 'same'),
    'POSITION': ('PERSONAL_ATTRIBUTE', 'tentative', 'job title; same'),
    'ZIPCODE': (None, 'unmapped', 'no project type; part of an address but not the same span'),
    'ALIEN_NUMBER': (None, 'unmapped', 'foreigner registration number; KR_RRN semantics differ, undecided'),
    'EMPLOYEE_ID': (None, 'unmapped', 'internal identifier; no project type'),
    'MEMBER_ID': (None, 'unmapped', 'service member id; no project type'),
    'PARTICIPANT_ID': (None, 'unmapped', 'event participant id; no project type'),
}
POLICIES = {'confirmed_only': {'confirmed'}, 'confirmed_and_tentative': {'confirmed', 'tentative'}}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_files(root, lock):
    root = Path(root)
    problems = []
    for item in lock['files']:
        path = root / item['path']
        if path.is_symlink() or not path.is_file():
            problems.append(f'missing:{item["path"]}')
        elif path.stat().st_size != item['size_bytes'] or sha256_file(path) != item['sha256']:
            problems.append(f'mismatch:{item["path"]}')
    return problems


def validate_raw_row(row, labels=LABEL_MAP):
    """Return a list of defects; the span is checked against the raw text only."""
    if (not isinstance(row, dict) or not isinstance(row.get('source_text'), str)
            or not isinstance(row.get('privacy_mask'), list) or type(row.get('id')) is not int):
        return ['invalid_row_schema']
    defects, text = [], row['source_text']
    for span in row['privacy_mask']:
        if not isinstance(span, dict) or span.get('label') not in labels:
            defects.append('unknown_label')
            continue
        a, b = span.get('start'), span.get('end')
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
            defects.append('invalid_span_range')
        elif text[a:b] != span.get('value'):
            defects.append('surface_mismatch')
    return defects


def to_project_row(row, accepted_status, labels=LABEL_MAP):
    """Map the candidate row to project types, or return ``(None, reason)``.

    Gold is never deleted: any label outside ``accepted_status`` rejects the document.
    """
    entities = []
    for span in row['privacy_mask']:
        kind, status, _ = labels[span['label']]
        if status not in accepted_status or kind is None:
            return None, f'label_not_mapped:{span["label"]}'
        entities.append({'type': kind, 'start': span['start'], 'end': span['end'], 'surface': span['value']})
    return {'text': row['source_text'], 'entities': entities}, None


def load_rows(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return [strict_json(line) for line in handle]


def overlap_indexes(official_root, service_path=None):
    texts, surfaces = set(), set()
    for name in ('train', 'dev', 'test_track_a'):
        with (Path(official_root) / f'{name}.jsonl').open('r', encoding='utf-8') as handle:
            for line in handle:
                row = json.loads(line)
                texts.add(hashlib.sha256(row['text'].encode()).hexdigest())
                surfaces.update(e['surface'] for e in row['entities'] if len(e['surface']) >= MIN_SURFACE_LEN_FOR_LEAK)
    service = set()
    if service_path and Path(service_path).is_file():
        with Path(service_path).open('r', encoding='utf-8') as handle:
            service = {hashlib.sha256(json.loads(line)['text'].encode()).hexdigest() for line in handle}
    return texts, surfaces, service


def analyse(root, planners, official=None, labels=LABEL_MAP, policies=POLICIES, splits=SPLIT_FILES):
    """Counts only; no raw text is placed in the returned report."""
    from ..data.conversion import ConversionError, convert_record
    from ..data.manifest import MODEL_ROOT as _unused  # noqa: F401  (keeps import cycle explicit)
    plan_labels = strict_json((MODEL_ROOT / 'configs/conversion-plan.json').read_bytes())['label_mapping']
    report = {'splits': {}, 'cross_split_duplicate_texts': {}, 'overlap_with_project_data': {}}
    hashes = {}
    for split, relative in splits.items():
        rows = load_rows(Path(root) / relative)
        defects, raw_labels = Counter(), Counter()
        digests = Counter()
        overlapping = 0
        for row in rows:
            for d in validate_raw_row(row, labels):
                defects[d] += 1
            raw_labels.update(s.get('label') for s in row['privacy_mask'] if isinstance(s, dict))
            digests[hashlib.sha256(row['source_text'].encode()).hexdigest()] += 1
            spans = sorted((s['start'], s['end']) for s in row['privacy_mask'])
            overlapping += any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))
        hashes[split] = set(digests)
        entry = {'documents': len(rows), 'unique_texts': len(digests),
                 'duplicate_extra_occurrences': sum(n - 1 for n in digests.values()),
                 'documents_with_overlapping_spans': overlapping, 'raw_defects': dict(defects),
                 'span_count': sum(raw_labels.values()), 'raw_label_counts': dict(sorted(raw_labels.items())),
                 'documents_with_zero_spans': sum(1 for r in rows if not r['privacy_mask']),
                 'policies': {}}
        for policy_name, accepted in policies.items():
            project_rows, rejects = [], Counter()
            for row in rows:
                converted, reason = to_project_row(row, accepted, labels)
                if converted is None:
                    rejects[reason] += 1
                else:
                    project_rows.append(converted)
            by_planner = {}
            for planner_id, planner in planners.items():
                reasons, kept, unique, by_type = Counter(), 0, set(), Counter()
                for row in project_rows:
                    try:
                        convert_record(row, planner, plan_labels)
                    except ConversionError as exc:
                        reasons[exc.code] += 1
                        continue
                    kept += 1
                    digest = hashlib.sha256(row['text'].encode()).hexdigest()
                    if digest not in unique:
                        unique.add(digest)
                        by_type.update(e['type'] for e in row['entities'])
                by_planner[planner_id] = {'convertible_documents': kept, 'unique_convertible_texts': len(unique),
                                          'rejected_by_conversion': dict(reasons),
                                          'gold_entities_by_project_type_in_unique_convertible': dict(sorted(by_type.items()))}
            entry['policies'][policy_name] = {'mapped_documents': len(project_rows),
                                              'rejected_for_unmapped_labels': sum(rejects.values()),
                                              'unmapped_label_reasons': dict(rejects.most_common()),
                                              'by_planner': by_planner}
        report['splits'][split] = entry
        if official is not None:
            texts, surfaces, service = official
            report['overlap_with_project_data'][split] = {
                'exact_text_with_k_pii_bench': sum(1 for d in digests if d in texts),
                'exact_text_with_service_set': sum(1 for d in digests if d in service),
                'long_surface_entities_in_k_pii_bench': sum(
                    1 for row in rows for s in row['privacy_mask'] if len(s['value']) >= MIN_SURFACE_LEN_FOR_LEAK
                    and s['value'] in surfaces)}
    names = list(hashes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            report['cross_split_duplicate_texts'][f'{a}+{b}'] = len(hashes[a] & hashes[b])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=MODEL_ROOT / '.cache/townboy-korean-pii' / CANDIDATE_REVISION)
    parser.add_argument('--tokenizer-dir', type=Path, required=True)
    parser.add_argument('--official-root', type=Path)
    parser.add_argument('--service-set', type=Path)
    parser.add_argument('--candidates', nargs='+', default=['whitespace_default_v1', 'ko_atomic_suffix_v3'])
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists():
        print(json.dumps({'status': 'failed', 'error_code': 'report_already_exists'}))
        return 2
    lock = strict_json(LOCK_PATH.read_bytes())
    problems = verify_files(args.root, lock)
    if problems:
        print(json.dumps({'status': 'failed', 'error_code': 'candidate_integrity_mismatch', 'problems': problems}))
        return 2
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')

    def deny(*unused, **kw):
        raise RuntimeError('offline_network_denied')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    from ..segmentation.planner import load_candidate_planner
    plan_labels = strict_json((MODEL_ROOT / 'configs/conversion-plan.json').read_bytes())['label_mapping']
    planners = {c: load_candidate_planner(args.tokenizer_dir, plan_labels, c) for c in args.candidates}
    official = overlap_indexes(args.official_root, args.service_set) if args.official_root else None
    report = analyse(args.root, planners, official)
    report.update({'schema_version': '1.0', 'report_id': 'candidate-townboy-v1', 'status': 'candidate_not_adopted',
                   'dataset': CANDIDATE_REPO, 'revision': CANDIDATE_REVISION, 'license': lock['license'],
                   'lock_sha256': sha256_file(LOCK_PATH), 'mapping_status_by_label': {
                       k: {'project_type': v[0], 'status': v[1], 'note': v[2]} for k, v in LABEL_MAP.items()},
                   'policies': {k: sorted(v) for k, v in POLICIES.items()},
                   'candidates': {c: p.metadata['preprocessing_version'] if 'preprocessing_version' in p.metadata else c
                                  for c, p in planners.items()},
                   'adopted': False, 'k_pii_bench_replaced': False, 'test_split_used_for_training': False})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({'status': report['status'], 'report': str(args.report)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

