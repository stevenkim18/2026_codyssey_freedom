"""Compare splitter candidates on train/dev only (never test) with exact conversion.

Counts are exact consequences of ``convert_record`` (whole-document accept or
reject). Raw text is not written to the report. No model weights, inference or
training are involved, so these are representability counts, not quality.
"""
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import socket
from pathlib import Path

from ..data.conversion import ConversionError, convert_record, _tokens as checked_tokens
from ..data.manifest import DEFAULT_LOCK, MODEL_ROOT, load_dataset_lock, strict_json
from ..data.convert_dataset import _alignment_profile
from .splitters import whitespace_default_v1

SELECTION_SPLITS = ('train', 'dev')   # test_track_a is deliberately excluded
REPORT_VERSION = 'segmentation-diagnosis-v1'


def restore_and_verify(row, converted, label_mapping):
    """Independently map every output span back to original offsets and compare."""
    inverse = {value: key for key, value in label_mapping.items()}
    seen = set()
    for example, window in zip(converted['examples'], converted['windows'], strict=True):
        if len(example['ner']) != len(window['gold_entity_indices']):
            return False
        for (left, right, label), index in zip(example['ner'], window['gold_entity_indices'], strict=True):
            gold = row['entities'][index]
            start = window['word_offsets'][left][0]
            end = window['word_offsets'][right][1]
            if (inverse[label], start, end) != (gold['type'], gold['start'], gold['end']):
                return False
            seen.add(index)
    return seen == set(range(len(row['entities'])))


def diagnose_split(path, planner, labels, required=None, progress=None):
    summary = {'source_documents': 0, 'source_gold_entities': 0, 'converted_documents': 0,
               'rejected_documents': 0, 'examples': 0, 'converted_source_gold_entities': 0,
               'unique_converted_texts': 0, 'roundtrip_failures': 0,
               'rejection_reasons': Counter(), 'source_by_type': Counter({k: 0 for k in labels}),
               'preserved_by_type': Counter({k: 0 for k in labels}),
               'unique_documents_with_type': Counter({k: 0 for k in labels}),
               'alignment_by_type': {key: Counter({k: 0 for k in labels}) for key in ('aligned', 'unaligned', 'too_wide')},
               'max_words_in_window': 0, 'total_words': 0, 'default_total_words': 0}
    unique = {}
    with Path(path).open('r', encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            row = strict_json(line)
            summary['source_documents'] += 1
            summary['source_gold_entities'] += len(row['entities'])
            summary['source_by_type'].update(e['type'] for e in row['entities'])
            try:
                tokenized = checked_tokens(planner, row['text'])
                summary['total_words'] += len(tokenized)
                summary['default_total_words'] += len(whitespace_default_v1(row['text']))
                for group, counts in _alignment_profile(row, tokenized, planner.max_span).items():
                    summary['alignment_by_type'][group].update(counts)
                converted = convert_record(row, planner, labels)
            except ConversionError as exc:
                summary['rejected_documents'] += 1
                summary['rejection_reasons'][exc.code] += 1
                continue
            if not restore_and_verify(row, converted, labels):
                summary['roundtrip_failures'] += 1
            summary['converted_documents'] += 1
            summary['examples'] += len(converted['examples'])
            summary['converted_source_gold_entities'] += len(row['entities'])
            summary['preserved_by_type'].update(e['type'] for e in row['entities'])
            digest = hashlib.sha256(row['text'].encode()).digest()
            if digest not in unique:
                unique[digest] = {e['type'] for e in row['entities']}
            summary['max_words_in_window'] = max([summary['max_words_in_window']]
                                                  + [len(x['tokenized_text']) for x in converted['examples']])
            if progress and number % 50000 == 0:
                progress(number)
    summary['unique_converted_texts'] = len(unique)
    summary['word_count_ratio_vs_default'] = (None if not summary['default_total_words']
                                              else round(summary['total_words'] / summary['default_total_words'], 6))
    for kinds in unique.values():
        summary['unique_documents_with_type'].update(kinds)
    summary['preservation_rate_by_type'] = {
        k: (None if not summary['source_by_type'][k] else round(summary['preserved_by_type'][k] / summary['source_by_type'][k], 6))
        for k in labels}
    summary['types_with_source_support_but_zero_preserved'] = sorted(
        k for k in labels if summary['source_by_type'][k] and not summary['preserved_by_type'][k])
    if (summary['converted_documents'] + summary['rejected_documents'] != summary['source_documents']
            or summary['converted_source_gold_entities'] != sum(summary['preserved_by_type'].values())):
        raise ValueError('conversion_ledger_mismatch')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokenizer-dir', type=Path, required=True)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--candidates', nargs='+', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists():
        print(json.dumps({'status': 'failed', 'error_code': 'report_already_exists'}))
        return 2
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    attempts = []

    def deny(*unused, **kw):
        attempts.append(1)
        raise RuntimeError('offline_network_denied')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    from .planner import load_candidate_planner
    dataset = load_dataset_lock(DEFAULT_LOCK)
    plan = strict_json((MODEL_ROOT / 'configs/conversion-plan.json').read_bytes())
    labels = plan['label_mapping']
    root = args.data_root or MODEL_ROOT / '.cache/k-pii-bench' / dataset['revision']
    report = {'schema_version': '1.0', 'report_version': REPORT_VERSION,
              'created_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'dataset_revision': dataset['revision'], 'label_mapping_version': plan['label_mapping_version'],
              'selection_splits': list(SELECTION_SPLITS), 'test_track_a_used': False,
              'scope': 'Exact representability only; no model weights/inference/training; not quality.',
              'required_unique_train_documents': plan['required_unique_training_documents'], 'candidates': {}}
    for candidate in args.candidates:
        planner = load_candidate_planner(args.tokenizer_dir, labels, candidate)
        entry = {'planner': planner.metadata, 'splits': {}}
        for split in SELECTION_SPLITS:
            entry['splits'][split] = diagnose_split(root / dataset['splits'][split]['path'], planner, labels,
                                                    progress=lambda n, c=candidate, s=split: print(c, s, n, flush=True))
        train = entry['splits']['train']
        entry['gates'] = {
            'unique_train_documents': train['unique_converted_texts'],
            'minimum_train_count_met': train['unique_converted_texts'] >= plan['required_unique_training_documents'],
            'train_types_with_source_support_but_zero_preserved': train['types_with_source_support_but_zero_preserved'],
            'all_supported_train_types_preserved': not train['types_with_source_support_but_zero_preserved'],
            'roundtrip_failures': train['roundtrip_failures'] + entry['splits']['dev']['roundtrip_failures'],
            'a_b_adopted': False, 'training_ready': False,
        }
        report['candidates'][candidate] = entry
    report['network_connection_attempts'] = len(attempts)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps({c: e['gates'] for c, e in report['candidates'].items()}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
