"""Rebuild static scientific figures from measured JSON, without model calls.

Raw predictions/text remain in ignored caches. The exported graph-data.json
contains aggregates and numeric measurements only, never document identifiers.
"""
import argparse
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).parents[1]/'.cache/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

from .runner import sha256_file


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev-root',type=Path,required=True,help='directory containing the three dev run directories')
    parser.add_argument('--final-root',type=Path,help='directory containing final-service/official-m0/m1-v1')
    parser.add_argument('--output',type=Path,default=Path('model/graph'))
    args=parser.parse_args(argv)
    font=Path('/System/Library/Fonts/AppleSDGothicNeo.ttc')
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'axes.unicode_minus':False,'svg.fonttype':'path','font.size':10,'figure.dpi':150})
    args.output.mkdir(parents=True,exist_ok=True)
    files=[];sources={};export={'scope':'Measured aggregates; dev is selection only. Offline timings exclude network and B client acceptance.'}
    def load(path):
        sources[str(path)]=sha256_file(path)
        return json.loads(path.read_text())
    def save(fig,name,title):
        fig.suptitle(title);fig.tight_layout()
        for suffix in ('png','svg'):fig.savefig(args.output/(name+'.'+suffix),bbox_inches='tight')
        plt.close(fig);files.append((name,title))
    recovery=load(Path('model/reports/stage7_5-recovery-v2.json'))
    state=next(v for k,v in recovery['trainer_states'].items() if 'checkpoint-1262/' in k)
    history=[r for r in state['log_history'] if 'loss' in r]
    export['training_history']=history
    fig,axes=plt.subplots(1,3,figsize=(13,3.8))
    for ax,key,label in zip(axes,('loss','learning_rate','grad_norm'),('학습 loss','기록된 learning rate','기록된 gradient norm')):
        ax.plot([r['step'] for r in history],[r[key] for r in history],marker='.',color='#3264a8')
        ax.axvline(631,ls='--',color='gray',label='epoch 1 경계');ax.set(xlabel='기록된 global step',ylabel=label);ax.grid(alpha=.2)
    axes[0].legend();save(fig,'training-history','원본 trainer_state 기록 — 성공한 optimizer update 수와 같다고 단정하지 않음')
    sample=load(Path('model/reports/stage7_5-sample-v1.json'))['summary']
    old=recovery['sample']['train_5k.jsonl'];export['samples']={'original_used':old,'new_not_trained':sample}
    for key,name,title in [('domains','sample-domains','학습에 사용한 기존 표본과 새로 준비한 표본: 분야 분포'),('entities_by_type','sample-labels','표본 유형별 gold 개수 — 새 표본은 아직 학습에 사용하지 않음')]:
        labels=sorted(set(old[key])|set(sample[key]));x=np.arange(len(labels));fig,ax=plt.subplots(figsize=(12,4.6))
        ax.bar(x-.2,[old[key].get(k,0) for k in labels],.4,label='기존 M1 학습에 사용')
        ax.bar(x+.2,[sample[key].get(k,0) for k in labels],.4,label='새 표본: 준비만 완료')
        ax.set_xticks(x,labels,rotation=55,ha='right');ax.set_ylabel('개수');ax.legend();ax.grid(axis='y',alpha=.2)
        save(fig,name,title)
    dev=[]
    for name in ('dev-m0-v1','dev-m1-epoch1-v1','dev-m1-epoch2-v1'):
        result=load(args.dev_root/name/'result.json')
        dev.append({'name':name,'profiles':[{'threshold':p['threshold'],'f1':p['score']['overall_micro']['f1'],'critical_recall':p['score']['critical_micro']['recall'],'normal_failure':p['gateway']['model_only']['normal_false_block']['rate']} for p in result['dev_threshold_profiles']]})
    export['dev']=dev;fig,axes=plt.subplots(1,3,figsize=(13,4))
    for run in dev:
        for ax,key in zip(axes,('f1','critical_recall','normal_failure')):
            ax.plot([p['threshold'] for p in run['profiles']],[100*p[key] for p in run['profiles']],marker='o',label=run['name'])
    for ax,title in zip(axes,('전체 F1 (%)','중요 5종 재현율 (%)','정상 차단 또는 오류 (%)')):
        ax.set(xlabel='공통 임계값',ylabel=title);ax.grid(alpha=.2)
    axes[0].legend(fontsize=7);save(fig,'dev-thresholds','Dev 설정 선택 자료 — 최종 시험 결과가 아님')
    if args.final_root:
        export['final']={}
        for split in ('service','official'):
            if not all((args.final_root/f'final-{split}-{role}-v1/result.json').exists() for role in ('m0','m1')):
                export['final'][split]={'status':'not_yet_complete_no_score_published'}
                continue
            runs=[load(args.final_root/f'final-{split}-{role}-v1/result.json') for role in ('m0','m1')]
            aggregate=[]
            for r in runs:
                aggregate.append({'experiment_id':r['experiment_id'],'documents':r['documents'],'inference_status_counts':r['inference_status_counts'],'score':r['score'],
                                  'gateway':{mode:{k:v for k,v in data.items() if k!='traces'} for mode,data in r['gateway'].items()}})
            export['final'][split]=aggregate
            fig,ax=plt.subplots(figsize=(7,4));x=np.arange(3)
            for i,r in enumerate(runs):ax.bar(x+(i-.5)*.35,[100*r['score']['overall_micro'][k] for k in ('precision','recall','f1')],.35,label=('M0','M1')[i])
            ax.set_xticks(x,['Precision','Recall','F1']);ax.set(ylabel='%',ylim=(0,105));ax.legend()
            save(fig,split+'-overall',split+' 최종 시험: 전체 엔터티 성능')
            labels=list(runs[0]['score']['per_type'])
            fig,axes=plt.subplots(1,2,figsize=(13,8));y=np.arange(len(labels))
            for ax,key in zip(axes,('recall','f1')):
                for i,r in enumerate(runs):
                    vals=[100*r['score']['per_type'][k][key] if (r['score']['per_type'][k]['gold'] and r['score']['per_type'][k][key] is not None) else np.nan for k in labels]
                    ax.barh(y+(i-.5)*.35,vals,.35,label=('M0','M1')[i])
                for idx,k in enumerate(labels):
                    if not runs[0]['score']['per_type'][k]['gold']:
                        ax.text(2,idx,'N/A (gold=0)',va='center',fontsize=8,color='#555555')
                ax.set_yticks(y,labels);ax.set(xlabel=key+' (%)',xlim=(0,105));ax.legend();ax.grid(axis='x',alpha=.2)
            save(fig,split+'-per-type',split+' 유형별 성능 — gold 0인 유형은 N/A 표시')
            modes=('model_only','model_plus_same_rules');fig,axes=plt.subplots(1,2,figsize=(11,4))
            for ax,mode in zip(axes,modes):
                bottoms=np.zeros(2)
                for action in ('allow','mask','block','error'):
                    counts=np.array([r['gateway'][mode]['actions'].get(action,0) for r in runs]);ax.bar(['M0','M1'],counts,bottom=bottoms,label=action);bottoms+=counts
                ax.set_title(mode);ax.set_ylabel('문서 수');ax.legend()
            save(fig,split+'-actions',split+' 실제 B 오프라인 정책 경로 결과: 1회 재검사 포함')
            fig,ax=plt.subplots(figsize=(7,4))
            timing=[]
            for role,r in zip(('M0','M1'),runs):
                values=np.sort([t['offline_inference_policy_recheck_ms'] for t in r['gateway']['model_only']['traces']])
                ax.plot(values,np.arange(1,len(values)+1)/len(values),label=role)
                timing.append({'model':role,'p50_ms':float(np.quantile(values,.5)),'p95_ms':float(np.quantile(values,.95)),'p99_ms':float(np.quantile(values,.99))})
            export['final'][split+'_offline_latency']=timing
            ax.set(xlabel='오프라인 추론+정책+재검사 ms (비캐시 model_only)',ylabel='누적 문서 비율',xscale='log');ax.legend();ax.grid(alpha=.2)
            save(fig,split+'-latency','서버 내부 측정: 네트워크 제외, B 클라이언트 p95 인수와 별개')
            fig,axes=plt.subplots(1,2,figsize=(11,4))
            for ax,mode in zip(axes,modes):
                has_normal=False
                for i,r in enumerate(runs):
                    g=r['gateway'][mode];n=g['normal_false_block'];ci=n['wilson_95'];rate=100*n['rate'] if n['normal_documents'] else np.nan
                    if n['normal_documents']:
                        has_normal=True
                        ax.errorbar(i,rate,yerr=[[rate-100*ci['low']],[100*ci['high']-rate]],fmt='o',capsize=5)
                        ax.annotate(f"{n['false_blocks']}/{n['normal_documents']}",(i,rate),xytext=(5,7),textcoords='offset points')
                if not has_normal:
                    ax.text(0.5,0.5,'정상 문서 0건 — 평가 불가 (N/A)',ha='center',va='center',transform=ax.transAxes,fontsize=10,color='#555555')
                ax.set_xticks([0,1],['M0','M1']);ax.set(xlim=(-.5,1.5),ylabel='정상 차단 또는 오류 (%)',title=mode);ax.grid(alpha=.2)
            save(fig,split+'-normal-failure',split+' 정상 문서 실패율과 Wilson 95% 구간 — 분모 0이면 평가 불가')
            fig,ax=plt.subplots(figsize=(9,4));x=np.arange(2)
            for i,mode in enumerate(modes):
                vals=[100*r['gateway'][mode]['residual_rate'] if r['gateway'][mode]['forwarded_protected_gold_entities'] else np.nan for r in runs]
                bars=ax.bar(x+(i-.5)*.35,vals,.35,label=mode)
                for bar,r in zip(bars,runs):
                    g=r['gateway'][mode]
                    if g['forwarded_protected_gold_entities']:ax.annotate(f"{g['residual_protected_entities']}/{g['forwarded_protected_gold_entities']}",(bar.get_x()+bar.get_width()/2,bar.get_height()),ha='center',va='bottom')
            ax.set_xticks(x,['M0','M1']);ax.set_ylabel('전달 문서 내 보호 대상 gold 잔존율 (%)');ax.margins(y=0.16);ax.legend();ax.grid(axis='y',alpha=.2)
            save(fig,split+'-residual','차단·오류 문서는 분모에서 제외 — allow/mask 전달 문서의 보호 대상만 계산')
    for split_key,ci_file,fig_name,fig_title in [
        ('service_paired_ci','model/reports/stage7_5-service-final-v1.json','service-paired-deltas','동결 서비스 300건: 문서 짝지은 변화량 구간 — 정상 실패 증가는 낮을수록 좋음'),
        ('official_paired_ci','model/reports/stage7_5-official-final-v1.json','official-paired-deltas','공식 Test 28,142건: 문서 짝지은 변화량 구간 — 정상 문서 없음(N/A)'),
    ]:
        ci_path=Path(ci_file)
        if ci_path.exists():
            ci=load(ci_path)['paired_ci'];export[split_key]=ci
            items=list(ci['metrics'].items());fig,ax=plt.subplots(figsize=(10,5))
            for i,(name,data) in enumerate(items):
                if data['estimate_pp'] is None:
                    ax.text(1.0,i,'N/A (정상 문서 0건)',va='center',fontsize=9,color='#555555')
                    continue
                point=data['estimate_pp'];ax.errorbar(point,i,xerr=[[point-data['low_pp']],[data['high_pp']-point]],fmt='o',capsize=4)
            ax.axvline(0,color='gray',ls='--');ax.set_yticks(range(len(items)),[k for k,_ in items]);ax.set_ylim(-0.7,len(items)-0.3);ax.set_xlabel('M1 - M0 (%p), paired document bootstrap 95%');ax.grid(axis='x',alpha=.2)
            save(fig,fig_name,fig_title)
    export['source_sha256']=sources
    (args.output/'graph-data.json').write_text(json.dumps(export,ensure_ascii=False,indent=2)+'\n')
    text='# 그래프 재현 및 해석\n\n실측 JSON으로 작성합니다. 새 표본은 학습 전이고 dev는 설정 선택용입니다. 최종 시험 결과를 보고 설정을 바꾸지 않습니다. 원문·문서 ID·키는 그래프에 넣지 않습니다.\n\n'
    text+='실행: `model/.cache/conversion-venv/bin/python -m model.evaluation.build_graphs --dev-root <dev-run-directory> [--final-root <final-run-directory>]`\n\n'
    text+='Matplotlib '+matplotlib.__version__+', NumPy '+np.__version__+'. PNG와 글꼴을 경로로 변환한 SVG를 함께 제공합니다. 입력 SHA-256과 수치: [graph-data.json](graph-data.json).\n\n'
    text+='| 그림 | PNG | SVG |\n|---|---|---|\n'+''.join(f'| {title} | [PNG]({name}.png) | [SVG]({name}.svg) |\n' for name,title in files)
    (args.output/'README.md').write_text(text)


if __name__=='__main__':main()
