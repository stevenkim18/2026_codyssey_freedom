"""Offline B gateway policy+one-reinspection evaluation; no upstream/network.

Reuses Gateway._inspect verbatim with an in-memory journal and a local model
adapter. Normal fail-closed errors count as transmission failures, separately
from explicit blocks. Source hashes/protocol distinguish this from v1 policy-
only false_block metrics. Nothing here attests B's production acceptance.
"""
import asyncio
from collections import Counter
import time
import hashlib
import json
from types import SimpleNamespace
from pathlib import Path
import sys

from . import metrics, policy_metrics
from .runner import document_id, validate_prediction_coverage


class _Journal:
    def append(self,event):
        pass  # B supplies redacted event counts; no external journal is used.


async def _measure(rows,pred_rows,backend,mode,only_normals,inference_cache):
    # B starts with dev/ on PYTHONPATH; journal.py uses its top-level gateway
    # imports. Use that supported package layout without editing B's source.
    dev_root=str(Path(__file__).parents[2]/'dev')
    if dev_root not in sys.path:
        sys.path.insert(0,dev_root)
    from gateway.engine import Gateway,GatewayError
    from gateway import policy
    predictions={row['document_id']:row for row in pred_rows}
    actions=Counter();normal_actions=Counter();traces=[]
    forwarded_gold=residual=normal_masks=reinspection_failures=0
    for row in rows:
        normal=not row.get('entities',[])
        if only_normals and not normal:continue
        rid=document_id(row);initial=predictions[rid]
        document={'document_id':rid,'text':row['text'],'gold':[]}
        adapter=policy_metrics.secret_rule_adapter if mode=='model_plus_same_rules' else None
        initial_decision=policy_metrics.decide(document,initial['findings'],rule_adapter=adapter)
        calls=cache_hits=0
        async def inspect(segments,direction,request_id,inspection_id):
            nonlocal calls,cache_hits
            calls+=1
            if calls==1:
                status=initial['status'];findings=initial['findings']
            else:
                cache_key=hashlib.sha256(json.dumps([backend.manifest['model_version'],backend.manifest['threshold_version'],segments],sort_keys=True).encode()).hexdigest()
                if inference_cache is not None and cache_key in inference_cache:
                    result=inference_cache[cache_key];cache_hits+=1
                else:
                    result=await asyncio.to_thread(backend.inspect,segments)
                    if inference_cache is not None and result.status=='complete':inference_cache[cache_key]=result
                status=result.status
                findings=[{'type':f.type,'start':f.start,'end':f.end,'score':f.score}
                          for f in result.findings] if status=='complete' else []
            merged=[dict(f,segment_id=rid,detector='model') for f in findings]
            if adapter is not None:
                merged+=list(adapter(segments[0]['text'],rid))
            return {'schema_version':'1.0','request_id':request_id,'inspection_id':inspection_id,
                    'status':status,'findings':merged,'timing_ms':0,
                    'model_version':backend.manifest['model_version'],
                    'threshold_version':backend.manifest['threshold_version']}
        gateway=Gateway(inspect,None,_Journal(),allowed_models=['offline-evaluation'])
        tick=time.monotonic();error=None
        try:
            direction='inbound' if row.get('direction')=='response' else 'outbound'
            decision,_,_,_=await gateway._inspect([{'segment_id':rid,'text':row['text']}],direction,'offline-'+rid,tick)
            action=decision.action
        except GatewayError as exc:
            error=exc.code
            action='block' if error=='POLICY_BLOCK' else 'error'
        actions[action]+=1
        if normal:
            normal_actions[action]+=1
            normal_masks+=initial_decision.action=='mask'
            reinspection_failures+=error=='REINSPECTION_FAILED'
        gold=[e for e in row.get('entities',[]) if e['type'] in policy.BLOCK_TYPES|policy.MASK_TYPES]
        missed=0
        if action in ('allow','mask'):
            covered=set()
            if initial_decision.action=='mask':
                for finding in initial_decision.findings:
                    if finding['type'] in policy.MASK_TYPES:
                        covered.update(range(finding['start'],finding['end']))
            missed=sum(not set(range(e['start'],e['end']))<=covered for e in gold)
            forwarded_gold+=len(gold);residual+=missed
        traces.append({'document_id':rid,'normal':normal,'first_action':initial_decision.action,
                       'final_action':action,'error_code':error,'inspection_calls':calls,
                       'cached_reinspection_calls':cache_hits,
                       'forwarded_protected_gold':len(gold) if action in ('allow','mask') else 0,
                       'residual_protected_entities':missed,
                       'offline_inference_policy_recheck_ms':initial.get('timing_ms',0)+(time.monotonic()-tick)*1000})
    normals=sum(normal_actions.values())
    failures=normal_actions['block']+normal_actions['error']
    return {'protocol':'B-gateway-one-reinspection-v1','mode':mode,
            'scope':'normal documents only' if only_normals else 'all supplied documents; no external transmission',
            'actions':dict(actions),'normal_false_block':{'false_blocks':failures,'normal_documents':normals,
                'explicit_blocks':normal_actions['block'],'fail_closed_errors':normal_actions['error'],
                'reinspection_failures':reinspection_failures,'rate':metrics.ratio(failures,normals),
                'wilson_95':metrics.wilson_interval(failures,normals),
                'definition':'normal documents not forwarded due to block OR error; differs from v1 block-only'},
            'normal_wrong_masks':normal_masks,'forwarded_protected_gold_entities':forwarded_gold,
            'residual_protected_entities':residual,'residual_rate':metrics.ratio(residual,forwarded_gold),
            'residual_scope':'protected gold in allow or successfully masked documents; blocked/error documents not forwarded',
            'traces':traces,'B_runtime_acceptance':None}


def gateway_metrics(rows,pred_rows,backend,*,mode='model_only',only_normals=False,inference_cache=None):
    if mode not in ('model_only','model_plus_same_rules'):
        raise ValueError('unknown_policy_report_mode')
    validate_prediction_coverage(rows,pred_rows)
    result=asyncio.run(_measure(rows,pred_rows,backend,mode,only_normals,inference_cache))
    result['cached_reinspection_calls']=sum(row['cached_reinspection_calls'] for row in result['traces'])
    result['latency_scope']='Cached mode timings are not independent end-to-end latency measurements; use uncached model_only traces.'
    return result
