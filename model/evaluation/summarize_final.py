"""Export complete final comparisons and paired intervals without raw text/IDs."""
import argparse
import json
from pathlib import Path

from .compare import compare,critical_regressions,replacement_decision
from .paired_ci import paired_intervals
from .runner import InferenceRunError,sha256_file


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--split',choices=('service','official'),required=True)
    p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    read=lambda path:json.loads(path.read_text())
    dirs=[args.run_root/f'final-{args.split}-{role}-v1' for role in ('m0','m1')]
    runs=[read(path/'result.json') for path in dirs]
    digest=sha256_file(args.dataset)
    if any(r['purpose']!='final' or r['status']!='executed' or r['conditions']['dataset_sha256']!=digest for r in runs):
        raise InferenceRunError('final_dataset_or_execution_mismatch')
    rows=[json.loads(line) for line in args.dataset.open()]
    preds=[[json.loads(line) for line in (path/'predictions.jsonl').open()] for path in dirs]
    if any(sum(r['inference_status_counts'].values())!=len(rows) or r['inference_status_counts'].get('complete')!=len(rows) for r in runs):
        raise InferenceRunError('incomplete_final_coverage')
    summary={'scope':f'Completed {args.split} final only; B acceptance is separate.',
             'source_sha256':{'dataset':digest,**{str(path/'result.json'):sha256_file(path/'result.json') for path in dirs},
                              **{str(path/'predictions.jsonl'):sha256_file(path/'predictions.jsonl') for path in dirs}},
             'models':[{k:r[k] for k in ('experiment_id','purpose','status','condition_id','conditions','artifacts_sha256','documents','inference_status_counts','score')}|
                       {'gateway':{mode:{k:v for k,v in data.items() if k!='traces'} for mode,data in r['gateway'].items()}} for r in runs],
             'paired_ci':paired_intervals(rows,*preds,baseline_gateway=runs[0]['gateway']['model_only'],current_gateway=runs[1]['gateway']['model_only']),
             'comparisons':{}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for mode in ('model_only','model_plus_same_rules'):
        records=[read(path/(mode+'.record.json')) for path in dirs]
        comparison=compare(records[1],records[0])
        if not comparison['comparable']:raise InferenceRunError('uncontrolled_final_comparison')
        summary['comparisons'][mode]={'comparison':comparison,'decision':replacement_decision(records[1],records[0]),
                                      'critical_regressions':critical_regressions(records[1],records[0])}
        for role,record in zip(('m0','m1'),records):
            path=args.output.parent/f'stage7_5-{args.split}-{role}-{mode}.record.json'
            path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    if not any(not row.get('entities',[]) for row in rows):
        summary['normal_failure_scope']='Not evaluable: no normal documents; use frozen service holdout for that replacement gate.'
    args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'split':args.split,'documents':len(rows),'output':str(args.output)}))


if __name__=='__main__':main()
