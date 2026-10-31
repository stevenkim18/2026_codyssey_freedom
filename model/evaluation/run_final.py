"""Run locked M0/M1 service+official final evaluations without retuning.

Only completed DEV selection artifacts are accepted. Each model is loaded in a
fresh process. Optional resource observations sample this process only and are
not a substitute for B's client-side latency/memory acceptance.
"""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from .runner import InferenceRunError,require_frozen_holdout,sha256_file


def observe(pid):
    sample={'elapsed_clock':time.monotonic(),'gpu_mib':None,'rss_kib':None}
    status=Path('/proc')/str(pid)/'status'
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith('VmRSS:'):sample['rss_kib']=int(line.split()[1])
    try:
        query=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],
                             capture_output=True,text=True,timeout=3)
        for line in query.stdout.splitlines():
            fields=[p.strip() for p in line.split(',')]
            if len(fields)==2 and fields[0]==str(pid):sample['gpu_mib']=float(fields[1])
    except (OSError,ValueError,subprocess.TimeoutExpired):
        pass
    return sample


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--holdout-manifest',type=Path,required=True)
    parser.add_argument('--holdout-dataset',type=Path,required=True)
    parser.add_argument('--official-test',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    args=parser.parse_args(argv)
    selection=json.loads(args.selection.read_text());frozen=json.loads(args.holdout_manifest.read_text())
    require_frozen_holdout(frozen,sha256_file(args.holdout_dataset))
    if selection['selection_split']!='dev_only' or selection['holdout_sha256']!=frozen['sha256']:
        raise InferenceRunError('selection_or_holdout_mismatch')
    if datetime.fromisoformat(selection['selected_at_utc'])<datetime.fromisoformat(frozen['frozen_at_utc']):
        raise InferenceRunError('selection_predates_freeze')
    for path,digest in selection['source_result_sha256'].items():
        if sha256_file(path)!=digest:raise InferenceRunError('selected_dev_artifact_changed')
    selected=selection['selected']
    args.output_root.mkdir(parents=True,exist_ok=False)
    batch={'started_at_utc':datetime.now(timezone.utc).isoformat(),'selection_sha256':sha256_file(args.selection),
           'script_sha256':sha256_file(__file__),'runs':[],'scope':'Locked final settings; no adaptation to final outcomes.'}
    for split,dataset,version,revision in [
        ('service',args.holdout_dataset,'service-holdout-v2-r2-frozen','authored-service-holdout-v2-r2'),
        ('official',args.official_test,'kpii-test-track-a-v1','844f92f7beba98fa964f957ba324017e82451701')]:
        for role in ('m0','m1'):
            name=f'final-{split}-{role}-v1';output=args.output_root/name
            command=[sys.executable,'-m','model.evaluation.run_checkpoint','--model-dir',selected[role+'_model_dir'],
                     '--manifest',str(args.manifest),'--evidence',str(args.evidence),'--output-dir',str(output),
                     '--experiment-id',name,'--purpose','final','--dataset',str(dataset),
                     '--model-role',role,'--dataset-version',version,'--dataset-revision',revision,
                     '--threshold',str(selected['threshold']),'--holdout-manifest',str(args.holdout_manifest),
                     '--holdout-dataset',str(args.holdout_dataset)]
            if role=='m1':command+=['--baseline-experiment-id',f'final-{split}-m0-v1']
            process=subprocess.Popen(command)
            observations=[]
            while process.poll() is None:
                observations.append(observe(process.pid));time.sleep(1)
            info={'experiment_id':name,'exit_code':process.returncode,'samples':len(observations),
                  'gpu_observed_max_mib':max((x['gpu_mib'] for x in observations if x['gpu_mib'] is not None),default=None),
                  'rss_observed_max_kib':max((x['rss_kib'] for x in observations if x['rss_kib'] is not None),default=None),
                  'sampling_scope':'~1-second process observations; observed max is not guaranteed peak; network excluded.'}
            batch['runs'].append(info)
            (args.output_root/'batch-state.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n')
            if process.returncode:
                raise SystemExit(process.returncode)
            (output/'resource-observations.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')
    batch['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    (args.output_root/'batch-completed.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
