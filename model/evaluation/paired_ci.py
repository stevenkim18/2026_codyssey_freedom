"""Paired document bootstrap of measured final-model deltas (percentage points).

Resamples the same document indices for M0/M1. Normal failure deltas resample
the normal-document stratum. These are conditional benchmark intervals, not
real-world guarantees; document/template dependence is a reported limitation.
"""
from collections import Counter
import numpy as np

from .records import CRITICAL_TYPES
from .runner import document_id,validate_prediction_coverage


def _document_counts(row,predicted,allowed=None):
    gold=Counter((e['type'],e['start'],e['end']) for e in row.get('entities',[])
                 if allowed is None or e['type'] in allowed)
    pred=Counter((e['type'],e['start'],e['end']) for e in predicted
                 if allowed is None or e['type'] in allowed)
    tp=sum((gold&pred).values())
    return tp,sum(pred.values())-tp,sum(gold.values())-tp


def paired_intervals(rows,baseline,current,*,iterations=1000,seed=42,baseline_gateway=None,current_gateway=None):
    validate_prediction_coverage(rows,baseline);validate_prediction_coverage(rows,current)
    if not rows or iterations<100:
        raise ValueError('bootstrap_requires_documents_and_at_least_100_iterations')
    base={r['document_id']:r['findings'] for r in baseline}
    cur={r['document_id']:r['findings'] for r in current}
    scopes=[None,set(CRITICAL_TYPES)]+[{kind} for kind in CRITICAL_TYPES]
    names=['overall_f1','critical_micro_recall']+['recall:'+kind for kind in CRITICAL_TYPES]
    arrays=[]
    for predictions in (base,cur):
        arrays.append(np.asarray([[value for scope in scopes
                       for value in _document_counts(row,predictions[document_id(row)],scope)] for row in rows],dtype=float))
    def evaluate(values):
        shaped=values.reshape(-1,len(scopes),3)
        denominator=shaped[:,:,0]+shaped[:,:,2]
        denominator[:,0]=2*shaped[:,0,0]+shaped[:,0,1]+shaped[:,0,2]
        numerator=shaped[:,:,0].copy();numerator[:,0]*=2
        return np.divide(numerator,denominator,out=np.full_like(numerator,np.nan),where=denominator>0)
    rng=np.random.default_rng(seed);samples=[]
    for start in range(0,iterations,32):
        indices=rng.integers(0,len(rows),size=(min(32,iterations-start),len(rows)))
        left=arrays[0][indices].sum(axis=1);right=arrays[1][indices].sum(axis=1)
        samples.append(100*(evaluate(right)-evaluate(left)))
    values=np.concatenate(samples)
    estimate=100*(evaluate(arrays[1].sum(axis=0))-evaluate(arrays[0].sum(axis=0)))[0]
    def interval(data,point):
        data=data[np.isfinite(data)]
        return {'estimate_pp':None if not np.isfinite(point) else float(point),
                'low_pp':None if not len(data) else float(np.quantile(data,.025)),
                'high_pp':None if not len(data) else float(np.quantile(data,.975)),
                'valid_iterations':len(data)}
    result={'method':'paired document bootstrap, NumPy PCG64; normal subset stratified',
            'iterations':iterations,'seed':seed,'documents':len(rows),'metrics':{
                name:interval(values[:,i],estimate[i]) for i,name in enumerate(names)},
            'limitations':'Conditional on this synthetic/template-derived benchmark. Document dependence and sample size limit generalization.'}
    if baseline_gateway is not None and current_gateway is not None:
        normals=[document_id(row) for row in rows if not row.get('entities',[])]
        left={r['document_id']:r for r in baseline_gateway['traces']}
        right={r['document_id']:r for r in current_gateway['traces']}
        if normals:
            difference=np.array([int(right[rid]['final_action'] in ('block','error'))-
                                 int(left[rid]['final_action'] in ('block','error')) for rid in normals])
            indices=rng.integers(0,len(normals),size=(iterations,len(normals)))
            result['metrics']['normal_failure_rate']=interval(100*difference[indices].mean(axis=1),100*difference.mean())
        else:
            result['metrics']['normal_failure_rate']=interval(np.array([]),np.nan)
        result['normal_documents']=len(normals)
    return result
