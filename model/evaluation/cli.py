"""Command line entry points for the evaluation system (no model is ever called).

    python -m model.evaluation score --gold gold.jsonl --pred pred.jsonl [--with-policy] [--rules]
    python -m model.evaluation check-record record.json
    python -m model.evaluation compare current.json reference.json
    python -m model.evaluation decide candidate.json baseline.json [--runtime-accepted]

``gold.jsonl`` rows: ``{"document_id", "text", "entities": [{"type","start","end"}]}``.
``pred.jsonl`` rows: ``{"document_id", "status":"complete", "findings": [...]}``.
Scores computed here are scores of the *supplied predictions*; with synthetic input
they demonstrate the tool, not any model's quality.
"""
import argparse
import json
import sys
from pathlib import Path

from . import compare as compare_mod
from . import metrics, policy_metrics, records
from .runner import InferenceRunError, validate_prediction_coverage


def _jsonl(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def cmd_score(args):
    gold_rows, pred_rows = _jsonl(args.gold), _jsonl(args.pred)
    legacy=False
    if args.allow_legacy_predictions and pred_rows and all('status' not in row for row in pred_rows):
        legacy=True
        pred_rows=[dict(row,status='complete') for row in pred_rows]
    try:
        validate_prediction_coverage(gold_rows,pred_rows)
    except InferenceRunError as exc:
        print(json.dumps({'valid':False,'error':str(exc),'scope':'No score: predictions/status do not cover all documents.'}))
        return 2
    lengths = {r['document_id']: len(r['text']) for r in gold_rows}
    gold = [(r['document_id'], e['type'], e['start'], e['end']) for r in gold_rows for e in r['entities']]
    pred = [(r['document_id'], f['type'], f['start'], f['end']) for r in pred_rows for f in r['findings']]
    result = metrics.score(gold, pred, document_lengths=lengths, critical_types=records.CRITICAL_TYPES)
    result['document_count']=len(gold_rows)
    result['execution_status_verified']=not legacy
    if legacy:
        result['scope']='Historical prediction re-count only; execution status is unverified, not final evaluation evidence.'
    result['macro'] = metrics.macro_f1(result['per_type'])
    if 'critical_micro' in result:
        rec = result['critical_micro']
        result['critical_micro']['recall_wilson_95'] = metrics.wilson_interval(rec['tp'], rec['tp'] + rec['fn'])
    if args.with_policy:
        documents = [{'document_id': r['document_id'], 'text': r['text'],
                      'gold': [(e['type'], e['start'], e['end']) for e in r['entities']]} for r in gold_rows]
        predictions = {r['document_id']: r['findings'] for r in pred_rows}
        result['policy'] = {'model_only': policy_metrics.false_block_metrics(documents, predictions)}
        result['policy']['model_only']['masking_residual'] = policy_metrics.masking_residual(documents, predictions)
        if args.rules:
            result['policy']['model_plus_same_rules'] = policy_metrics.false_block_metrics(
                documents, predictions, mode='model_plus_same_rules', rule_adapter=policy_metrics.secret_rule_adapter)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_check(args):
    try:
        records.validate_record(_load_json(args.record))
    except records.RecordError as exc:
        print(json.dumps({'valid': False, 'errors': exc.codes}))
        return 1
    print(json.dumps({'valid': True}))
    return 0


def cmd_compare(args):
    print(json.dumps(compare_mod.compare(_load_json(args.current), _load_json(args.reference)), ensure_ascii=False, indent=2))
    return 0


def cmd_decide(args):
    result = compare_mod.replacement_decision(_load_json(args.candidate), _load_json(args.baseline),
                                              runtime_acceptance=True if args.runtime_accepted else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['verdict'] == 'replace' else 3


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m model.evaluation', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('score')
    p.add_argument('--gold', required=True)
    p.add_argument('--pred', required=True)
    p.add_argument('--with-policy', action='store_true')
    p.add_argument('--rules', action='store_true', help="also report model_plus_same_rules (B's secret rules)")
    p.add_argument('--allow-legacy-predictions',action='store_true',
                   help='Explicit historical re-count without original execution status; not final evidence')
    p.set_defaults(func=cmd_score)
    p = sub.add_parser('check-record')
    p.add_argument('record')
    p.set_defaults(func=cmd_check)
    p = sub.add_parser('compare')
    p.add_argument('current')
    p.add_argument('reference')
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser('decide')
    p.add_argument('candidate')
    p.add_argument('baseline')
    p.add_argument('--runtime-accepted', action='store_true')
    p.set_defaults(func=cmd_decide)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())

