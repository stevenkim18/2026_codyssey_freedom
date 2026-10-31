"""Append separate authored normal calibration cases to pinned official dev.

Final holdout scenarios/values are never imported. No model is called. Original
dev instances (including within-dev duplicates) are preserved without edits.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from .runner import InferenceRunError,sha256_file

TEMPLATES=[
 '이 배포 문서의 기준일은 {date}이고 점검 예상 시간은 {n}분입니다.',
 '시제품 제작 예산에서 소모품 비용 {amount}원을 별도 항목으로 집계합니다.',
 '배열 원소 {n}개를 역순으로 정렬한 다음 합계를 계산하는 예시를 작성해 주세요.',
 '입력 검사 설정은 최대 길이 {n}, 재시도 {small}회입니다. 값을 벗어나면 오류를 반환합니다.',
 '실험의 오차는 {small}%이며 전체 측정 횟수는 {n}회입니다. 중앙값과 평균을 비교합니다.',
 '회의 안건은 서식 개정과 문서 보관입니다. 시행일은 {date}로 적어 주세요.',
 '도서 {n}권과 빈 보관 상자 {small}개의 위치를 바꾸는 절차를 설명합니다.',
 '금융은 산업 분류이고 연구는 활동의 종류입니다. 이 두 보통명사를 {small}문장으로 풀이해 주세요.',
 '은행잎과 단풍잎의 색이 달라지는 과정을 {n}자 안팎으로 설명해 주세요.',
 '상품 분류 기호 MAT-{digits}에 속한 부품이 {small}개 부족합니다. 재고 메모를 정리합니다.',
 '{{"batch_limit": {n}, "attempts": {small}, "updated": "{date}"}}\n설정 항목의 단위를 주석으로 적어 주세요.',
 'limit = {n}\nvalues = [value for value in range({small})]\n이 코드는 숫자만 다루는 목록 예시입니다.',
 '작업,소요분,실패횟수\n압축,{n},{small}\n변환,{small},0\n합계를 계산합니다.',
 '| 재료 | 필요량 |\n|---|---:|\n| 종이 | {n}장 |\n| 잉크 | {small}병 |\n물품 목록을 정리합니다.',
 'The validation job starts on {date} and processes {n} rows. 결과 설명은 한국어로 작성해 주세요.',
 'The estimated cost is {amount} KRW. 배송비 포함 여부와 별개로 단순 예산 합계만 계산합니다.',
 '설정 파일 수정 전에는 복사본을 만들고 실패하면 이전 내용으로 복원합니다. 최대 {small}번 시도합니다.',
 '## 작업 순서\n검사, 정렬, 저장을 차례로 수행합니다. 예상 시간은 {n}초이고 기준일은 {date}입니다.',
 '모든 이름 입력란은 비워 둔 서식입니다. 행 수 {n}와 열 수 {small}만 표시해 주세요.',
 '설명용 정수 {digits}를 세 자리씩 끊어 읽는 방법을 적어 주세요. 금액이나 개인 식별값을 뜻하지 않는 계산 예제입니다.',
]


def normal_rows():
    rows=[]
    for i in range(200):
        family,variant=i%len(TEMPLATES),i//len(TEMPLATES)
        n=31+variant*19+family*3
        text=TEMPLATES[family].format(date=f'2025-{1+(i*7)%12:02d}-{1+(i*11)%28:02d}',
                  n=n,small=2+i%7,amount=f'{(n*1300+7900):,}',digits=str(8642000000000+i*9187))
        rows.append({'id':f'dev-normal-v1-{i:03d}','text':text,'entities':[],
                     'generation':{'purpose':'dev_calibration_only','template_id':f'dev-normal-family-{family}',
                                   'variant':variant,'source':'independently authored nonpersonal tasks'}})
    return rows


def prepare(source,output,holdout,legacy):
    source,output=Path(source),Path(output)
    root=Path(__file__).parents[1]
    lock=json.loads((root/'configs/assets.lock.json').read_text())['dataset']
    expected=next(x['digest']['value'] for x in lock['artifacts'] if x['path']=='data/dev.jsonl')
    if sha256_file(source)!=expected:raise InferenceRunError('official_dev_hash_mismatch')
    rows=normal_rows()
    reserved=[]
    for path in [holdout,legacy]:
        reserved += [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    normalize=lambda text:re.sub(r'\d','0',text)
    reserved_texts={r['text'] for r in reserved}
    reserved_skeletons={normalize(r['text']) for r in reserved if not r.get('entities')}
    if any(r['text'] in reserved_texts or normalize(r['text']) in reserved_skeletons for r in rows):
        raise InferenceRunError('dev_normal_overlaps_reserved_service_scenario')
    output.mkdir(parents=True,exist_ok=False)
    normal=output/'dev.normal.jsonl';combined=output/'dev.calibration.jsonl'
    normal.write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in rows))
    ids=set();count=0
    from .runner import document_id
    with combined.open('x') as out,source.open() as original:
        for line in original:
            row=json.loads(line);rid=document_id(row)
            if rid in ids:raise InferenceRunError('duplicate_dev_id')
            ids.add(rid);count+=1
            out.write(line if line.endswith('\n') else line+'\n')
        for row in rows:
            if row['id'] in ids:raise InferenceRunError('normal_id_collision')
        out.write(normal.read_text())
    manifest={'schema_version':'1.0','role':'dev_only','dataset_version':'kpii-dev-plus-normal-v1',
              'official_dev_sha256':expected,'normal_sha256':sha256_file(normal),
              'combined_sha256':sha256_file(combined),'official_documents':count,'normal_documents':len(rows),
              'combined_documents':count+len(rows),'normal_templates':len(TEMPLATES),
              'holdout_and_legacy_exact_or_normal_skeleton_overlap':0,
              'implementation_sha256':sha256_file(__file__),
              'scope':'Authored normal dev calibration cases, not a human-reviewed final benchmark; original dev preserved.'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--holdout',type=Path,required=True)
    parser.add_argument('--legacy',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(prepare(args.source,args.output_dir,args.holdout,args.legacy),ensure_ascii=False))


if __name__=='__main__':main()
