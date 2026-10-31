"""Fresh, hash-verified offline evaluation; never starts training or overwrites.

Smoke mode checks real loading, shared splitting, Unicode/long-text coverage
and reload behavior. It is not a final performance benchmark. Dev selection
and final evaluation require a separately human-reviewed, frozen holdout.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal

from . import metrics, policy_metrics, records
from .runner import (InferenceRunError, condition_id, document_id, require_frozen_holdout,
                     run_inference, sha256_file, validate_prediction_coverage)
from ..segmentation.runtime import attach_splitter


def verify_artifacts(root, evidence):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise InferenceRunError('invalid_model_directory')
    model_file = root / ('model.safetensors' if (root/'model.safetensors').is_file() else 'pytorch_model.bin')
    checked = {}
    for path in (model_file, root/'gliner_config.json', root/'tokenizer_config.json', root/'spm.model'):
        expected = evidence.get('artifacts', {}).get(str(path), {})
        if (not path.is_file() or path.is_symlink() or not expected.get('exists')
                or path.stat().st_size != expected['bytes'] or sha256_file(path) != expected['sha256']):
            raise InferenceRunError('model_artifact_hash_mismatch:' + path.name)
        checked[path.name] = expected['sha256']
    # Check optional local tokenizer/config assets too; they affect real loading.
    for name in ('tokenizer.json', 'config.json', 'added_tokens.json', 'special_tokens_map.json'):
        path = root/name
        if path.exists():
            expected = evidence.get('artifacts', {}).get(str(path), {})
            if not expected.get('exists') or path.is_symlink() or sha256_file(path) != expected.get('sha256'):
                raise InferenceRunError('model_artifact_hash_mismatch:' + name)
            checked[name] = expected['sha256']
    return checked


def load_experiment_backend(model_dir, manifest_path, evidence, *, splitter_id, threshold, device):
    """Explicit A-only loader; B's production loader/approval is not bypassed."""
    checked = verify_artifacts(model_dir, evidence)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    from gliner import GLiNER
    from ..segmentation.coverage_backend import CoverageBackend
    if importlib.metadata.version('gliner') != '0.2.29':
        raise InferenceRunError('unsupported_gliner_version')
    manifest = json.loads(Path(manifest_path).read_text())
    expected_manifest = evidence.get('artifacts', {}).get(str(manifest_path), {})
    if not expected_manifest.get('exists') or sha256_file(manifest_path) != expected_manifest.get('sha256'):
        raise InferenceRunError('package_manifest_hash_mismatch')
    weight_hash = checked.get('model.safetensors') or checked['pytorch_model.bin']
    manifest['model_version'] = 'offline-' + weight_hash[:16]
    manifest['thresholds'] = {key: threshold for key in manifest['label_mapping']}
    manifest['threshold_version'] = 'fixed-' + str(threshold) + '-diagnostic-v1'
    manifest['preprocessing']['version'] = 'a-seg-candidate-' + splitter_id
    model = GLiNER.from_pretrained(str(model_dir), local_files_only=True, strict=True,
                                   load_tokenizer=True, map_location=device)
    if any(parameter.device.type != device for parameter in model.parameters()):
        raise InferenceRunError('actual_model_device_mismatch')
    attach_splitter(model, splitter_id)
    model.eval()
    backend = CoverageBackend(model, manifest)
    backend.warmup()
    return backend, checked


def audit_windows(backend,rows):
    """Independently record accepted window budgets, not rejected fit probes."""
    audit=[]
    for row in rows:
        text=row['text']
        windows=backend._plan(text,[backend.max_plan_checks])
        coverage=set();word_max=subtoken_max=0
        for start,end in windows:
            words=[t[0] for t in backend._tokens(text[start:end])]
            prepared,_=backend.model.data_processor.prepare_inputs([words],backend.labels)
            encoded=backend.model.data_processor.transformer_tokenizer(prepared,is_split_into_words=True,
                                     truncation=False,padding=False,return_attention_mask=False)
            length=len(encoded['input_ids'][0])
            if len(words)>backend.max_words or length>backend.max_subtokens or not backend._fits(words):
                raise InferenceRunError('accepted_window_exceeds_budget')
            word_max=max(word_max,len(words));subtoken_max=max(subtoken_max,length)
            coverage.update(range(start,end))
        if any(not char.isspace() and i not in coverage for i,char in enumerate(text)):
            raise InferenceRunError('accepted_window_missing_text')
        audit.append({'document_id':document_id(row),'windows':len(windows),
                      'max_words':word_max,'max_subtokens_including_labels':subtoken_max,
                      'all_nonwhitespace_codepoints_covered':True})
    return audit


def smoke_rows():
    texts = ['김가상에게 연락해 주세요.', '김가상님과 김가상에게 각각 전달합니다.',
             '연락 주소는 pipeline.check@example.org입니다.',
             '예시 링크 https://example.org/check?q=synthetic 를 확인합니다.',
             '👩\u200d💻 김가상에게 cafe\u0301 표기를 전달합니다.',
             '시험 주민번호 990101-1234567과 전화 010-0000-0000을 확인합니다.',
             '재고 24개, 버전 v2.7.1, 배치 번호 LOT-731입니다.',
             '검사 자료입니다. ' * 120 + '김가상에게 마지막 내용을 전달합니다.']
    return [{'id': f'smoke-{i:02d}', 'text': text, 'entities': []} for i,text in enumerate(texts)]


def score_complete(rows, pred_rows):
    validate_prediction_coverage(rows, pred_rows)
    gold = [(document_id(r), e['type'], e['start'], e['end']) for r in rows for e in r.get('entities', [])]
    pred = [(r['document_id'], f['type'], f['start'], f['end']) for r in pred_rows for f in r['findings']]
    result = metrics.score(gold, pred, document_lengths={document_id(r):len(r['text']) for r in rows},
                           critical_types=records.CRITICAL_TYPES)
    result['document_count'] = len(rows)
    from .service_cases import TYPES
    for kind in TYPES:
        result['per_type'].setdefault(kind,{'tp':0,'fp':0,'fn':0,'gold':0,'predicted':0,
                                           'precision':None,'recall':None,'f1':None})
    for row in result['per_type'].values():
        row['recall_wilson_95'] = metrics.wilson_interval(row['tp'], row['gold'])
    normalized = [{'document_id':document_id(r), 'text':r['text'],
                   'gold':[(e['type'],e['start'],e['end']) for e in r.get('entities',[])]} for r in rows]
    predictions = {r['document_id']:r['findings'] for r in pred_rows}
    policy = {}
    for mode,adapter in [('model_only',None),('model_plus_same_rules',policy_metrics.secret_rule_adapter)]:
        policy[mode] = {'false_block':policy_metrics.false_block_metrics(normalized,predictions,mode=mode,rule_adapter=adapter),
                        'masking_residual':policy_metrics.masking_residual(normalized,predictions,mode=mode,rule_adapter=adapter)}
    return {'score':result,'policy':policy,
            'ci_scope':'Wilson entity recall intervals are descriptive; document-paired delta bootstrap still required.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--experiment-id',required=True)
    parser.add_argument('--purpose',choices=['smoke','regression','dev','final'],required=True)
    parser.add_argument('--dataset',type=Path)
    parser.add_argument('--holdout-manifest',type=Path)
    parser.add_argument('--holdout-dataset',type=Path)
    parser.add_argument('--splitter',default='ko_atomic_suffix_v3')
    parser.add_argument('--threshold',type=float,default=0.5)
    parser.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    parser.add_argument('--model-role',choices=['m0','m1'])
    parser.add_argument('--baseline-experiment-id')
    parser.add_argument('--dataset-version')
    parser.add_argument('--dataset-revision',default='844f92f7beba98fa964f957ba324017e82451701')
    parser.add_argument('--threshold-grid',type=float,nargs='+')
    args=parser.parse_args(argv)
    if not 0 <= args.threshold <= 1:
        parser.error('threshold must be in [0,1]')
    if args.threshold_grid and (args.purpose!='dev' or min(args.threshold_grid)!=args.threshold
                                or any(not 0<=value<=1 for value in args.threshold_grid)):
        parser.error('threshold-grid is dev-only and inference threshold must equal its minimum')
    if args.purpose in ('dev','final'):
        if not args.holdout_manifest or not args.holdout_dataset:
            parser.error('dev/final requires reviewed frozen holdout manifest and dataset')
        require_frozen_holdout(json.loads(args.holdout_manifest.read_text()),sha256_file(args.holdout_dataset))
    if args.purpose=='smoke':
        if args.dataset:
            parser.error('smoke uses built-in synthetic fixtures only')
        rows=smoke_rows()
        data_hash=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    else:
        if not args.dataset:
            parser.error('dataset is required')
        if not args.model_role or not args.dataset_version or (args.model_role=='m1' and not args.baseline_experiment_id):
            parser.error('scored runs require model-role, dataset-version, and M1 baseline-experiment-id')
        if 'test_track_a' in args.dataset.name and args.purpose!='final':
            parser.error('official final test cannot be used for selection/regression')
        rows=[json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
        data_hash=sha256_file(args.dataset)
        lock=json.loads((Path(__file__).parents[1]/'configs/assets.lock.json').read_text())['dataset']
        official_test=next(x['digest']['value'] for x in lock['artifacts'] if x['path']=='data/test_track_a.jsonl')
        if data_hash==official_test and args.purpose!='final':
            parser.error('official test hash cannot be used for selection, even if renamed')
        if args.purpose=='final' and data_hash not in (official_test,sha256_file(args.holdout_dataset)):
            parser.error('final data must be the pinned official test or the reviewed frozen holdout')
    evidence=json.loads(args.evidence.read_text())
    # Reserve output before model loading. A failed experiment remains visible.
    args.output_dir.mkdir(parents=True,exist_ok=False)
    started=datetime.now(timezone.utc).isoformat()
    (args.output_dir/'run-state.json').write_text(json.dumps({'pid':os.getpid(),'workspace':str(Path.cwd()),
           'experiment_id':args.experiment_id,'started_at_utc':started,'status':'running'},indent=2)+'\n')
    def interrupted(signum,frame):
        raise KeyboardInterrupt('offline evaluation interrupted')
    previous={sig:signal.signal(sig,interrupted) for sig in (signal.SIGTERM,signal.SIGHUP)}
    try:
        backend,artifacts=load_experiment_backend(args.model_dir,args.manifest,evidence,
                           splitter_id=args.splitter,threshold=args.threshold,device=args.device)
        common={'dataset_sha256':data_hash,'purpose':args.purpose,'labels':list(backend.manifest['label_mapping'].items()),
                'thresholds':backend.thresholds,'splitter':args.splitter,'max_words':backend.max_words,
                'max_span':backend.max_span,'max_subtokens':backend.max_subtokens,
                'rules':'b-secret-rules-v1','policy':'B-gateway-one-reinspection-v1','device':args.device,
                'packages':{p:importlib.metadata.version(p) for p in ('torch','gliner','transformers','tokenizers')},
                'source_sha256':{str(p):sha256_file(p) for p in [Path(__file__),Path(__file__).with_name('runner.py'),
                    Path(__file__).parents[1]/'segmentation/splitters.py',Path(__file__).parents[1]/'segmentation/runtime.py',
                    Path(__file__).with_name('metrics.py'),Path(__file__).with_name('policy_metrics.py'),
                    Path(__file__).with_name('gateway_metrics.py'),Path(__file__).with_name('experiment_record.py'),
                    Path(__file__).parents[1]/'segmentation/coverage_backend.py',
                    Path(__file__).parents[2]/'dev/detector/gliner_backend.py',
                    Path(__file__).parents[2]/'dev/gateway/policy.py',Path(__file__).parents[2]/'dev/gateway/engine.py',
                    Path(__file__).parents[2]/'dev/gateway/secret_rules.py']}}
        (args.output_dir/'run-provenance.json').write_text(json.dumps({'conditions':common,
              'condition_id':condition_id(common),'artifacts_sha256':artifacts,'started_at_utc':started},ensure_ascii=False,indent=2)+'\n')
        predictions,pred_rows=run_inference(backend,rows,args.experiment_id,prediction_path=args.output_dir/'predictions.jsonl')
        result={'schema_version':'1.0','experiment_id':args.experiment_id,'purpose':args.purpose,
                'status':'executed','started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),
                'condition_id':condition_id(common),'conditions':common,'artifacts_sha256':artifacts,
                'runtime_acceptance':None,'b_splitter_adoption':False,'documents':len(rows),
                'inference_status_counts':{'complete':len(rows),'incomplete':0,'error':0}}
        if args.purpose=='smoke':
            result['scope']='Actual pipeline loading/coverage only; no gold and no quality score.'
            result['fixture_token_offsets']={r['id']:list(backend.model.data_processor.words_splitter(r['text']))
                                             for r in rows if len(r['text'])<200}
            result['accepted_window_budget_audit']=audit_windows(backend,rows)
        else:
            result.update(score_complete(rows,pred_rows))
            from .gateway_metrics import gateway_metrics
            recheck_cache={}
            result['gateway']={mode:gateway_metrics(rows,pred_rows,backend,mode=mode,only_normals=args.purpose=='dev',inference_cache=recheck_cache)
                               for mode in ('model_only','model_plus_same_rules')}
            from .experiment_record import make_record
            baseline=args.baseline_experiment_id or args.experiment_id
            sample=(evidence.get('sample',{}).get('train_5k.jsonl',{}).get('sha256')
                    if args.model_role=='m1' else None)
            for mode in ('model_only','model_plus_same_rules'):
                record=make_record(result,model_role=args.model_role,baseline_id=baseline,
                    dataset_revision=args.dataset_revision,dataset_version=args.dataset_version,
                    sample_sha256=sample,report_mode=mode)
                (args.output_dir/(mode+'.record.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            if args.threshold_grid:
                grid=[]
                for cutoff in sorted(set(args.threshold_grid)):
                    backend.thresholds={kind:cutoff for kind in backend.thresholds}
                    backend.manifest['thresholds']=backend.thresholds
                    backend.manifest['threshold_version']=condition_id({'thresholds':backend.thresholds})
                    filtered=[dict(row,findings=[f for f in row['findings'] if f['score']>=cutoff]) for row in pred_rows]
                    measured=score_complete(rows,filtered)
                    recheck_cache={}
                    measured['gateway']={mode:gateway_metrics(rows,filtered,backend,mode=mode,only_normals=True,inference_cache=recheck_cache)
                                         for mode in ('model_only','model_plus_same_rules')}
                    grid.append({'threshold':cutoff,**measured})
                    print(json.dumps({'dev_profile':cutoff,'f1':measured['score']['overall_micro']['f1'],
                         'critical_recall':measured['score']['critical_micro']['recall'],
                         'normal_transmission_failure':measured['gateway']['model_only']['normal_false_block']['rate']}),flush=True)
                result['dev_threshold_profiles']=grid
                result['threshold_reuse_scope']='Low-threshold decoded findings filtered per cutoff; dev only. Final evaluation runs the chosen threshold directly.'
        (args.output_dir/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    except BaseException as exc:
        (args.output_dir/'failed.json').write_text(json.dumps({'status':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed','exception_type':type(exc).__name__,
             'experiment_id':args.experiment_id,'started_at_utc':started,
             'scope':'Not a valid scored experiment; inspect retained predictions/status and server log.'},indent=2)+'\n')
        raise
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    print(json.dumps({'experiment_id':args.experiment_id,'status':'executed','purpose':args.purpose,'documents':len(rows)}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
