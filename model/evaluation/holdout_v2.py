"""New authored service holdout DRAFT and explicit human-review freeze gate.

Reuses synthetic value/offset utilities, not v1 scenario templates. Never scores
the model or marks a generated case reviewed. Outputs go to an ignored folder.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

from .synthetic_values import FreshValues, render_natural
from .holdout_scenarios import scenario, NORMAL_FAMILIES
from .service_cases import TYPES, official_index, validate
from .records import CRITICAL_TYPES
from .runner import InferenceRunError, sha256_file

VERSION='service-holdout-v2-r2-draft'
SEED=2026110101
CONTEXTS=[
    '검진 예약 변경을 처리하기 위한 인수 메모', '장학금 지급 전 제출 자료 확인',
    '배송 사고 조사 담당자의 접수 기록', '임대 계약 갱신 서류의 검토 요청',
    '교육 과정 수료 확인 자료의 전달 기록', '분실물 반환 신청의 접수 내용',
    '대관 취소 환급 신청의 상담 메모', '이사 지원 신청의 첨부 정보 요약',
    '출입증 재발급 요청의 담당자 회신', '숙박 취소 처리에 관한 내부 인계',
    '체험 행사 참가 신청의 연락 기록', '복지 지원 심사 자료의 보완 요청',
    '원격 점검 일정 조정에 관한 작업 메모', '장비 반납 신청의 접수 기록',
    '정기 구독 변경 신청의 확인 문서', '창작 공모전 참가 서류의 검토 기록',
    '해외 연수 제출 자료의 확인 요청', '분할 납부 신청의 상담 기록',
    '이용권 명의 확인에 관한 전달 자료', '반려된 신청서의 보완 회신',
]


def _inject_chunk_boundary(row,planner,values,avoid):
    """Author a probe crossing an actual first-window edge; no model is used."""
    base=row['text']
    tokens=list(planner.tokens(base))
    first_edge=planner.windows(base)[0][1]
    last=next(i for i,t in enumerate(tokens) if t[2]==first_edge)
    card=values.CREDIT_CARD('space')
    while card in avoid:
        card=values.CREDIT_CARD('space')
    prefix=' 추가 결제 확인 자료의 카드번호: '
    insertion=prefix+card+' 확인 후 계속 진행합니다. '
    for distance in range(0,min(36,last)):
        cut=tokens[last-distance][1]
        if any(e['start']<cut<e['end'] for e in row['entities']):continue
        text=base[:cut]+insertion+base[cut:]
        start,end=cut+len(prefix),cut+len(prefix)+len(card)
        windows=planner.windows(text)
        if not any(start<edge<end for _,edge in windows):continue
        entities=[dict(e,start=e['start']+(len(insertion) if e['start']>=cut else 0),
                         end=e['end']+(len(insertion) if e['start']>=cut else 0)) for e in row['entities']]
        entities.append({'type':'CREDIT_CARD','start':start,'end':end,'surface':card})
        row['text']=text;row['entities']=sorted(entities,key=lambda e:e['start'])
        row['tags']+=['boundary','chunk_boundary']
        row['generation']['chunk_boundary_probe']={'gold_start':start,'gold_end':end,
            'crossed_window_edges':[edge for _,edge in windows if start<edge<end],
            'splitter_id':planner.splitter_id,'model_weights_used':False}
        return
    raise InferenceRunError('could_not_construct_chunk_overlap_probe')


def build(avoid=(),planner=None):
    rng=random.Random(SEED)
    values=FreshValues(rng)
    other=[t for t in TYPES if t not in CRITICAL_TYPES]
    rows=[]
    formats=['plain','table','json','code','csv','yaml','mixed_language','markdown','boundary','long']
    for i in range(200):
        # Rotate labels across formats; the original i%5 vs i%10 coupling was confounded.
        first=CRITICAL_TYPES[(i//10+3*i)%len(CRITICAL_TYPES)]
        second=other[(i*7+i//10)%len(other)]
        style=i%10
        values.current_birth=None
        text,entities=render_natural(scenario(CONTEXTS[i//10],first,second,style,i),values,avoid=avoid)
        tags=[formats[style],'has_pii','multi_entity']
        if style in (0,8):tags.append('particle')
        row={'id':f'holdout-v2r2-positive-{i:03d}','direction':'request' if i%2==0 else 'response',
             'text':text,'entities':entities,'tags':tags,
             'generation':{'method':'revised_authored_natural_scenarios','generator':VERSION,
                           'template_id':f'holdout-v2r2-workflow-{i//10:02d}-format-{style}',
                           'format_id':style,'seed':SEED},'review_status':'needs_review'}
        if style==9 and (i//10)%2==0 and planner is not None:
            _inject_chunk_boundary(row,planner,values,avoid)
        rows.append(row)
    for family_index,(family,texts) in enumerate(NORMAL_FAMILIES.items()):
        for variant,text in enumerate(texts):
            i=family_index*10+variant
            tags=['normal',family]
            if family=='source_code':tags.append('code')
            if family=='mixed_language':tags.append('mixed_language')
            if family=='tables_and_lists':tags.append('markdown')
            if family in ('business_calendar','amounts_and_quantities','numeric_lookalikes'):tags.append('hard_digits')
            rows.append({'id':f'holdout-v2r2-normal-{i:03d}','direction':'request' if i%2==0 else 'response',
                         'text':text,'entities':[],'tags':tags,
                         'generation':{'method':'authored_diverse_normal_scenarios','generator':VERSION,
                                       'template_id':f'holdout-v2r2-normal-{family}-{variant}',
                                       'normal_family':family,'seed':SEED},'review_status':'needs_review'})
    return rows


def write_draft(output,official_root,v1_path,*,planner=None,feedback_path=None):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    official=official_index(official_root)
    v1=[json.loads(line) for line in Path(v1_path).read_text().splitlines() if line.strip()]
    avoid=set(official[1])|{e['surface'] for row in v1 for e in row['entities'] if len(e['surface'])>=8}
    rows=build(avoid,planner)
    validation=validate(rows,official)
    v1_texts={row['text'] for row in v1}
    overlap=sum(row['text'] in v1_texts for row in rows)
    if validation['errors'] or overlap:
        raise InferenceRunError('draft_mechanical_validation_failed:'+','.join(validation['errors']))
    path=output/'service-holdout-v2.draft.jsonl'
    path.write_text(''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows))
    manifest={'dataset_version':VERSION,'sha256':sha256_file(path),'builder_sha256':sha256_file(__file__),
              'frozen':False,'reviewer':None,'frozen_at_utc':None,'validation':validation,
              'v1_exact_text_overlap':overlap,'v1_templates_reused':False,
               'supporting_source_sha256':{p.name:sha256_file(p) for p in [Path(__file__).with_name('synthetic_values.py'),Path(__file__).with_name('holdout_scenarios.py'),Path(__file__).with_name('service_testset_builder.py')]},
              'feedback_sha256':sha256_file(feedback_path) if feedback_path else None,
              'chunk_boundary_probes':sum('chunk_boundary' in r['tags'] for r in rows),
              'limitations':'Revised natural authored synthetic scenarios; new human review required. Issuance/allocation of synthetic identifiers is not claimed. No model scores obtained.',
              'use_policy':'No model selection or scoring before human review/freeze; v1 stays exploratory.'}
    (output/'manifest.draft.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    decisions={'dataset_sha256':manifest['sha256'],'reviewer':'',
               'decisions':[{'id':row['id'],'approved':False,'notes':''} for row in rows]}
    (output/'review-decisions.json').write_text(json.dumps(decisions,ensure_ascii=False,indent=2)+'\n')
    pages=['# 새 서비스 holdout v2 — 사람 검토용 초안\n',
           '각 문서의 gold 의미·범위와 정상 여부, 형식·다양성을 검토합니다. 누락·오분류가 있으면 승인하지 말고 수정 내용을 기록하세요.\n',
           'review-decisions.json에서 실제 검토한 각 ID의 approved와 notes를 기록합니다. 자동 승인은 하지 않습니다.\n']
    for row in rows:
        pages += [f"## {row['id']} · {row['direction']}\n",'````text\n'+row['text']+'\n````\n',
                  '정답: '+('; '.join(f"{e['type']} [{e['start']},{e['end']}): {e['surface']}" for e in row['entities']) or '없음 — 개인정보 없는 정상 문서인지 확인')+'\n']
    (output/'review.md').write_text('\n'.join(pages))
    return manifest


def freeze(draft_dir,decisions_path,output,official_root):
    draft_dir,output=Path(draft_dir),Path(output)
    source=draft_dir/'service-holdout-v2.draft.jsonl'
    decisions=json.loads(Path(decisions_path).read_text())
    rows=[json.loads(line) for line in source.read_text().splitlines()]
    approved=decisions.get('decisions',[])
    ids=[item.get('id') for item in approved]
    if (decisions.get('dataset_sha256')!=sha256_file(source) or not decisions.get('reviewer','').strip()
            or len(ids)!=len(set(ids)) or set(ids)!={row['id'] for row in rows}
            or any(item.get('approved') is not True for item in approved)):
        raise InferenceRunError('all_300_human_review_decisions_required')
    for row in rows:
        row['review_status']='reviewed'
    validation=validate(rows,official_index(official_root))
    if validation['status']!='ready_to_freeze' or validation['warnings']:
        raise InferenceRunError('reviewed_dataset_not_ready_to_freeze')
    output.mkdir(parents=True,exist_ok=False)
    path=output/'service-holdout-v2.frozen.jsonl'
    path.write_text(''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows))
    manifest={'dataset_version':'service-holdout-v2-frozen','sha256':sha256_file(path),
              'draft_sha256':sha256_file(source),'review_decisions_sha256':sha256_file(decisions_path),
              'reviewer':decisions['reviewer'],'frozen':True,
              'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'validation':validation}
    (output/'manifest.frozen.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--official-root',type=Path,required=True)
    parser.add_argument('--v1-dataset',type=Path)
    parser.add_argument('--tokenizer-dir',type=Path)
    parser.add_argument('--feedback',type=Path)
    parser.add_argument('--freeze-draft',type=Path)
    parser.add_argument('--review-decisions',type=Path)
    args=parser.parse_args(argv)
    if args.freeze_draft:
        if not args.review_decisions:parser.error('--review-decisions required')
        result=freeze(args.freeze_draft,args.review_decisions,args.output_dir,args.official_root)
    else:
        if not args.v1_dataset:parser.error('--v1-dataset required')
        planner=None
        if args.tokenizer_dir:
            from ..segmentation.planner import load_candidate_planner
            mapping=json.loads((Path(__file__).parents[1]/'configs/conversion-plan.json').read_text())['label_mapping']
            planner=load_candidate_planner(args.tokenizer_dir,mapping,'ko_atomic_suffix_v3')
        result=write_draft(args.output_dir,args.official_root,args.v1_dataset,planner=planner,feedback_path=args.feedback)
    print(json.dumps({'frozen':result['frozen'],'sha256':result['sha256'],'status':result['validation']['status']}))


if __name__=='__main__':
    main()
