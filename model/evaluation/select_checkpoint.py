"""Select a common-threshold M0/M1 pair using ONLY completed dev runs."""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path

from .records import CRITICAL_TYPES
from .runner import InferenceRunError,require_frozen_holdout,sha256_file


def profile_summary(profile):
    score=profile['score'];failure=profile['gateway']['model_only']['normal_false_block']
    return {'threshold':profile['threshold'],'overall':score['overall_micro'],
            'critical':score['critical_micro'],'normal_failure':failure,
            'per_critical':{kind:score['per_type'][kind] for kind in CRITICAL_TYPES}}


def eligibility(candidate,baseline):
    gates={'overall_f1':candidate['overall']['f1']>=baseline['overall']['f1'],
           'critical_recall':candidate['critical']['recall']>=baseline['critical']['recall'],
           'normal_failure_increase':candidate['normal_failure']['rate']-baseline['normal_failure']['rate']<=.01+1e-12}
    for kind in CRITICAL_TYPES:
        gates['recall:'+kind]=candidate['per_critical'][kind]['recall']>=baseline['per_critical'][kind]['recall']
    return gates


def choose(m0,candidates):
    pairs=[]
    baseline={p['threshold']:profile_summary(p) for p in m0['dev_threshold_profiles']}
    for epoch_index,run in enumerate(candidates,1):
        for profile in run['dev_threshold_profiles']:
            cur=profile_summary(profile);base=baseline[cur['threshold']]
            gates=eligibility(cur,base)
            rank=(min(base['overall']['f1'],cur['overall']['f1']),cur['overall']['f1'],
                  cur['critical']['recall'],-cur['normal_failure']['rate'],-cur['threshold'],-epoch_index)
            pairs.append({'candidate_experiment_id':run['experiment_id'],'epoch_index':epoch_index,
                          'threshold':cur['threshold'],'gates':gates,'eligible':all(gates.values()),
                          'rank':rank,'m0':base,'m1':cur})
    eligible=[pair for pair in pairs if pair['eligible']]
    winner=max(eligible or pairs,key=lambda pair:pair['rank'])
    return winner,pairs


def model_root(run,evidence):
    artifacts=run['artifacts_sha256']
    weight=artifacts.get('model.safetensors') or artifacts['pytorch_model.bin']
    options=[]
    for path,info in evidence['artifacts'].items():
        if info.get('sha256')==weight and Path(path).name in ('model.safetensors','pytorch_model.bin'):
            root=Path(path).parent
            if all(evidence['artifacts'].get(str(root/name),{}).get('sha256')==digest for name,digest in artifacts.items()):
                options.append(str(root))
    if not options:raise InferenceRunError('selected_artifacts_not_in_recovered_evidence')
    return sorted(options)[0],weight


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--m0-result',type=Path,required=True)
    parser.add_argument('--m1-results',type=Path,nargs=2,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--holdout-manifest',type=Path,required=True)
    parser.add_argument('--holdout-dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    paths=[args.m0_result]+args.m1_results
    runs=[json.loads(path.read_text()) for path in paths]
    manifest=json.loads(args.holdout_manifest.read_text())
    require_frozen_holdout(manifest,sha256_file(args.holdout_dataset))
    if any(r['status']!='executed' or r['purpose']!='dev' for r in runs):
        raise InferenceRunError('all_candidates_must_be_completed_dev_runs')
    if len({r['condition_id'] for r in runs})!=1:
        raise InferenceRunError('dev_conditions_not_controlled')
    frozen=datetime.fromisoformat(manifest['frozen_at_utc'])
    if any(datetime.fromisoformat(r['started_at_utc'])<frozen for r in runs):
        raise InferenceRunError('selection_runs_started_before_holdout_freeze')
    plan_path=Path(__file__).parents[1]/'configs/stage7_5-evaluation-plan.json'
    plan=json.loads(plan_path.read_text())
    for run in runs:
        if sorted(p['threshold'] for p in run['dev_threshold_profiles'])!=plan['same_threshold_grid_per_weight']:
            raise InferenceRunError('dev_search_grid_differs_from_locked_plan')
    winner,pairs=choose(runs[0],runs[1:])
    chosen=next(run for run in runs if run['experiment_id']==winner['candidate_experiment_id'])
    evidence=json.loads(args.evidence.read_text())
    m0_root,m0_sha=model_root(runs[0],evidence);m1_root,m1_sha=model_root(chosen,evidence)
    output={'schema_version':'1.0','selected_at_utc':datetime.now(timezone.utc).isoformat(),
            'selection_split':'dev_only','holdout_sha256':manifest['sha256'],'plan_sha256':sha256_file(plan_path),
            'source_result_sha256':{str(path):sha256_file(path) for path in paths},
            'selected':{'threshold':winner['threshold'],'m0_model_dir':m0_root,'m0_weights_sha256':m0_sha,
                        'm1_model_dir':m1_root,'m1_weights_sha256':m1_sha,
                        'm1_experiment_id':winner['candidate_experiment_id'],'epoch_index':winner['epoch_index'],
                        'dev_eligible':winner['eligible'],'dev_gates':winner['gates']},
            'pairs':pairs,'scope':'Dev selection only. No final-test result read; B runtime acceptance pending.',
            'training_scope':'Existing M1 checkpoints used; new unique 5k sample is prepared but not trained.'}
    with args.output.open('x') as handle:handle.write(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(output['selected'],ensure_ascii=False))


if __name__=='__main__':main()
