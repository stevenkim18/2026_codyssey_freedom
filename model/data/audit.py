"""Stream the pinned dataset and report structure/quality without raw examples."""
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import argparse
import hashlib
import json
import platform
import re
import time

from .manifest import DEFAULT_LOCK, MODEL_ROOT, SPLITS, ManifestError, load_dataset_lock, strict_json

DOMAINS = frozenset(('support_ticket', 'document', 'education', 'government', 'email_thread',
                     'medical', 'telecommunications', 'employment', 'legal', 'chat', 'financial', 'insurance'))
REQUIRED_TYPES = {'doc_id': str, 'template_id': str, 'domain': str, 'locale': str,
                  'text': str, 'template_text': str, 'entities': list, 'meta': dict,
                  'tokens': list, 'bio_tags': list, 'token_offsets': list}
PLACEHOLDER = re.compile(r'<<[A-Z][A-Z0-9_]*:\d+>>')


def _length_summary(values):
    count = sum(values.values())
    if not count:
        return {'min': None, 'max': None, 'mean': None, 'p50': None, 'p95': None}
    ordered = sorted(values)
    def percentile(p):
        rank = max(1, (count*p+99)//100)
        total = 0
        for size in ordered:
            total += values[size]
            if total >= rank:
                return size
    return {'min': ordered[0], 'max': ordered[-1],
            'mean': round(sum(k*v for k,v in values.items())/count, 3),
            'p50': percentile(50), 'p95': percentile(95)}


def audit_dataset(data_root, lock_path=DEFAULT_LOCK, *, statistics_path=None, sample_limit=20):
    if type(sample_limit) is not int or not 0 <= sample_limit <= 1000:
        raise ValueError('invalid_sample_limit')
    started = time.monotonic()
    dataset = load_dataset_lock(lock_path)
    labels = set(dataset['canonical_labels'])
    bio_labels = {'O'} | {'B-'+x for x in labels} | {'I-'+x for x in labels}
    root = Path(data_root)
    artifacts = {x['path']: x for x in dataset['artifacts']}
    errors, warnings = Counter(), Counter()
    examples = []
    seen = {key: {} for key in ('doc_id', 'text', 'template_id', 'template_text')}
    template_skeletons = {}
    gold_signatures = {}
    overlap = {a+'__'+b: {key:set() for key in seen} for a,b in combinations(SPLITS,2)}
    split_results = {}

    def issue(code, split, line=None, *, warning=False, entity=None):
        (warnings if warning else errors)[code] += 1
        if len(examples) < sample_limit:
            item = {'code': code, 'severity': 'review' if warning else 'error', 'split': split}
            if line is not None:
                item['line'] = line
            if entity is not None:
                item['entity_index'] = entity
            examples.append(item)

    for split_index, split in enumerate(SPLITS):
        info = dataset['splits'][split]
        item = artifacts[info['path']]
        path = root / info['path']
        summary = {'documents': 0, 'physical_lines': 0, 'entity_count': 0, 'negative_documents': 0,
                   'label_counts': Counter({label:0 for label in labels}),
                   'documents_per_label': Counter({label:0 for label in labels}), 'domain_counts': Counter(),
                   'over_gateway_limit_documents': 0, 'unique_doc_ids': 0, 'unique_texts': 0,
                   'unique_template_ids': 0, 'unique_template_skeletons': 0,
                   'duplicate_text_groups': 0, 'duplicate_text_occurrences': 0,
                   'duplicate_annotation_conflicts': 0, 'labels_without_entities': sorted(labels)}
        split_results[split] = summary
        lengths = Counter()
        duplicate_groups = set()
        digest = hashlib.sha256()
        size = 0
        bit = 1 << split_index
        if root.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            issue('missing_file' if not path.exists() else 'symlink_file', split)
            summary['integrity'] = {'matches': False, 'expected_sha256': item['digest']['value'],
                                    'expected_bytes': item['size_bytes'], 'actual_sha256': None, 'actual_bytes': None}
            summary['text_length'] = _length_summary(lengths)
            continue
        with path.open('rb') as handle:
            for line_number, raw in enumerate(handle, 1):
                digest.update(raw)
                size += len(raw)
                summary['physical_lines'] += 1
                try:
                    row = strict_json(raw.decode('utf-8'))
                except (UnicodeError, ValueError, RecursionError):
                    issue('invalid_json_or_utf8', split, line_number)
                    continue
                if not isinstance(row, dict):
                    issue('invalid_record_schema', split, line_number)
                    continue
                summary['documents'] += 1
                if any(key not in row or type(row[key]) is not kind for key,kind in REQUIRED_TYPES.items()):
                    issue('invalid_record_schema', split, line_number)
                    continue
                if not all(row[key] for key in ('doc_id','template_id','text','template_text')):
                    issue('empty_required_string', split, line_number)
                text = row['text']
                lengths[len(text)] += 1
                summary['over_gateway_limit_documents'] += int(len(text)>16000)
                if row['domain'] not in DOMAINS:
                    issue('unknown_domain', split, line_number)
                else:
                    summary['domain_counts'][row['domain']] += 1
                if row['locale'] != 'ko-KR':
                    issue('unexpected_locale', split, line_number)
                if PLACEHOLDER.search(text):
                    issue('unreplaced_placeholder', split, line_number)

                try:
                    identifiers = {key:hashlib.sha256(row[key].encode('utf-8')).digest() for key in seen}
                except UnicodeError:
                    issue('invalid_unicode_string', split, line_number)
                    continue
                for key, hashed in identifiers.items():
                    mask = seen[key].get(hashed, 0)
                    if mask & bit and key in {'doc_id','text'}:
                        issue('duplicate_'+key+'_within_split', split, line_number, warning=key=='text')
                        if key == 'text':
                            duplicate_groups.add(hashed)
                            summary['duplicate_text_occurrences'] += 1
                    if not mask & bit:
                        summary[{'doc_id':'unique_doc_ids','text':'unique_texts','template_id':'unique_template_ids','template_text':'unique_template_skeletons'}[key]] += 1
                    for previous_index in range(split_index):
                        if mask & (1 << previous_index):
                            pair = SPLITS[previous_index]+'__'+split
                            if hashed not in overlap[pair][key]:
                                overlap[pair][key].add(hashed)
                                issue('cross_split_'+key, split, line_number)
                    seen[key][hashed] = mask | bit
                previous = template_skeletons.setdefault(identifiers['template_id'], identifiers['template_text'])
                if previous != identifiers['template_text']:
                    issue('template_id_skeleton_conflict', split, line_number)

                if not row['entities']:
                    summary['negative_documents'] += 1
                spans = []
                entity_keys = set()
                document_labels = set()
                for entity_index, entity in enumerate(row['entities']):
                    summary['entity_count'] += 1
                    if not isinstance(entity, dict) or any(key not in entity for key in ('type','start','end','surface')):
                        issue('invalid_entity_schema', split, line_number, entity=entity_index)
                        continue
                    kind = entity['type']
                    if not isinstance(kind, str) or kind not in labels:
                        issue('unknown_entity_type', split, line_number, entity=entity_index)
                    else:
                        summary['label_counts'][kind] += 1
                        document_labels.add(kind)
                    a,b = entity['start'],entity['end']
                    if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
                        issue('invalid_entity_span', split, line_number, entity=entity_index)
                        continue
                    if not isinstance(entity['surface'], str) or text[a:b] != entity['surface']:
                        issue('entity_surface_mismatch', split, line_number, entity=entity_index)
                    identity = (a,b,kind) if isinstance(kind,str) else (a,b,None)
                    if identity in entity_keys:
                        issue('duplicate_entity', split, line_number, warning=True, entity=entity_index)
                    entity_keys.add(identity)
                    spans.append((a,b))
                summary['documents_per_label'].update(document_labels)
                gold = sorted((kind,a,b) for a,b,kind in entity_keys if kind in labels)
                signature = hashlib.sha256(json.dumps(gold,separators=(',',':')).encode()).digest()
                previous_gold = gold_signatures.setdefault(identifiers['text'],signature)
                if previous_gold != signature:
                    summary['duplicate_annotation_conflicts'] += 1
                    issue('duplicate_text_annotation_conflict',split,line_number)
                for i,(a,b) in enumerate(spans):
                    for c,d in spans[i+1:]:
                        if (a,b)!=(c,d) and max(a,c)<min(b,d):
                            issue('overlapping_entity_pair', split, line_number, warning=True)

                tokens, tags, offsets = row['tokens'],row['bio_tags'],row['token_offsets']
                if not len(tokens)==len(tags)==len(offsets) or (text and not tokens):
                    issue('token_alignment_length', split, line_number)
                if any(not isinstance(token,str) for token in tokens):
                    issue('invalid_token_type', split, line_number)
                if any(not isinstance(tag,str) or tag not in bio_labels for tag in tags):
                    issue('unknown_bio_tag', split, line_number)
                previous_end=0
                for offset in offsets:
                    if (not isinstance(offset,list) or len(offset)!=2 or any(type(x) is not int for x in offset)
                            or not 0<=offset[0]<offset[1]<=len(text)):
                        issue('invalid_token_offset', split, line_number)
                    elif offset[0]<previous_end:
                        issue('token_offsets_overlap_or_unordered', split, line_number)
                    else:
                        previous_end=offset[1]

        summary['integrity'] = {'matches': size==item['size_bytes'] and digest.hexdigest()==item['digest']['value'],
                                'expected_sha256': item['digest']['value'], 'expected_bytes': item['size_bytes'],
                                'actual_sha256': digest.hexdigest(), 'actual_bytes': size}
        if not summary['integrity']['matches']:
            issue('file_integrity_mismatch', split)
        if summary['documents'] != info['expected_documents_from_upstream_metadata']:
            issue('document_count_mismatch', split)
        summary['text_length'] = _length_summary(lengths)
        summary['duplicate_text_groups'] = len(duplicate_groups)
        summary['labels_without_entities'] = sorted(label for label in labels if not summary['label_counts'][label])
        for key in ('label_counts','documents_per_label','domain_counts'):
            summary[key] = dict(sorted(summary[key].items()))

    if statistics_path is not None:
        statistics = strict_json(Path(statistics_path).read_bytes())
        for split in SPLITS:
            expected = statistics['splits'][split]
            actual = split_results[split]
            if actual['domain_counts'] != expected['domains']:
                issue('domain_statistics_mismatch', split)
            if actual['unique_template_skeletons'] != expected['skeleton_count']:
                issue('skeleton_statistics_mismatch', split)
    status = 'failed' if errors else 'needs_review' if warnings else 'passed'
    return {
        'schema_version': '1.0', 'status': status,
        'created_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'repo_id': dataset['repo_id'], 'revision': dataset['revision'],
        'assets_lock_sha256': hashlib.sha256(Path(lock_path).read_bytes()).hexdigest(),
        'upstream_statistics_sha256': hashlib.sha256(Path(statistics_path).read_bytes()).hexdigest() if statistics_path else None,
        'implementation_sha256': {name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                                  for name in ('audit.py','manifest.py')},
        'python_version': platform.python_version(), 'elapsed_seconds': round(time.monotonic()-started,3),
        'scope': 'Full-file integrity and all-record structural/gold-span/distribution/duplicate checks. No model predictions, tokenizer conversion or model-quality measurements.',
        'test_split_use': 'Structural quality audit only; no model or threshold selection.',
        'total_documents': sum(x['documents'] for x in split_results.values()),
        'splits': split_results,
        'cross_split_overlap': {pair:{key:len(value) for key,value in fields.items()} for pair,fields in overlap.items()},
        'errors': dict(sorted(errors.items())), 'warnings': dict(sorted(warnings.items())),
        'issue_examples': examples, 'issue_example_limit': sample_limit,
        'report_privacy': 'No raw document text, entity surface values, document IDs or per-record text hashes are exported. Whole-file hashes identify the fixed public dataset files.',
        'ready_for_conversion_without_review': status=='passed', 'training_performed': False,
    }


def write_report(result, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--statistics', type=Path, default=MODEL_ROOT/'metadata/dataset-split_statistics.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(json.dumps({'status':'failed','error_code':'report_already_exists'}))
        return 2
    try:
        lock = load_dataset_lock(args.lock)
        root = args.data_root or MODEL_ROOT/'.cache/k-pii-bench'/lock['revision']
        result = audit_dataset(root,args.lock,statistics_path=args.statistics)
        write_report(result,args.output)
        print(json.dumps({'status':result['status'],'total_documents':result['total_documents'],
                          'errors':result['errors'],'warnings':result['warnings'],'report':str(args.output)},ensure_ascii=False))
        return 0 if result['status']=='passed' else 1
    except (ManifestError,OSError,ValueError,KeyError,TypeError):
        print(json.dumps({'status':'failed','error_code':'audit_input_or_report_error'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
