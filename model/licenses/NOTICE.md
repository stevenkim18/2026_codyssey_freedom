# 외부 자원 출처와 라이선스 기록

기록일: 2026-10-31. 이 문서는 1단계에서 확인한 원본의 표시와 사본 출처를 기록합니다. 라이선스 사본은 원문 그대로 보관했습니다.

| 자원 | 원본의 표시 | 보관한 근거 |
|---|---|---|
| `urchade/gliner_multi_pii-v1` 모델 | 모델 카드의 `apache-2.0` | [모델 카드 사본](../metadata/m0-MODEL_CARD.md) |
| GLiNER `0.2.29` 코드 | Apache-2.0 | [코드 저장소 LICENSE 사본](Apache-2.0-GLiNER.txt) |
| `microsoft/mdeberta-v3-base` | 모델 카드의 `mit` | [모델 카드 사본](../metadata/backbone-MODEL_CARD.md), [Microsoft LICENSE 사본](MIT-DeBERTa.txt) |
| K-PII-Bench 데이터 | CC BY 4.0 | [데이터 LICENSE-DATA 사본](CC-BY-4.0-DATA.txt) |

## 원본 출처

- M0: [고정 모델 카드](https://huggingface.co/urchade/gliner_multi_pii-v1/blob/1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/README.md). 모델 저장소에는 별도의 LICENSE 파일이 없으므로, 모델 카드의 선언과 GLiNER 코드의 라이선스 사본을 구분합니다. 코드의 LICENSE를 모델 저장소에서 받은 파일이라고 표시하지 않습니다.
- GLiNER 코드: [고정 LICENSE](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/LICENSE).
- 기반 모델: [고정 모델 카드](https://huggingface.co/microsoft/mdeberta-v3-base/blob/a0484667b22365f84929a935b5e50a51f71f159d/README.md).
- Microsoft 라이선스 사본: [고정 LICENSE](https://github.com/microsoft/DeBERTa/blob/4d7fe0bd4fb3c7d4f4005a7cafabde9800372098/LICENSE). B가 기록한 라이선스 파일의 SHA-256과 일치함을 확인했습니다.
- 데이터: [고정 LICENSE-DATA](https://huggingface.co/datasets/woohyun212/k-pii-bench/blob/844f92f7beba98fa964f957ba324017e82451701/LICENSE-DATA).

## 데이터 저자 표시

K-PII-Bench. Woohyun Park, Jinyoung Oh, Jeong-Won Cha, Changwon National University, 2026.

데이터 원본: `woohyun212/k-pii-bench`, revision `844f92f7beba98fa964f957ba324017e82451701`, 통계 파일의 버전명 `v4-corrected`.

데이터 원본은 [CC BY 4.0](CC-BY-4.0-DATA.txt)으로 표시되어 있습니다. 원본 저자·출처·라이선스와 이후 변환·수정 내용을 전달물과 발표자료에도 기록합니다. 데이터 코드의 라이선스와 데이터 자체의 라이선스는 구분합니다.

## 1단계에서 한 변경

- 원본 모델 가중치와 학습 레코드는 아직 다운로드·변경하지 않았습니다.
- 설명서·JSON 설정·API 메타데이터·라이선스는 변경하지 않은 사본으로 보관했습니다.
- Python 패키지 메타데이터는 필요한 필드를 추려 새 JSON으로 기록했습니다. 원본 응답과 편집한 기록의 확인값을 구분했습니다.
- A의 자산 기준·도구 계획·실험 계획과 한국어 안내 문서를 새로 작성했습니다.
- 학습 데이터 변환과 추가 학습은 아직 수행하지 않았습니다. 이후에는 사용한 원본·변환 방식·학습 설정·날짜를 추가 기록합니다.

각 사본의 조회 URL·조회 시각·파일 SHA-256은 [출처 목록](../metadata/sources.json)에 있습니다. 전체 학습 환경이 확정되면 실제 설치한 하위 패키지까지 외부 자원 목록에 추가합니다.

## 2단계에서 한 변경

- 위에 고정한 리비전의 `data/train.jsonl`, `data/dev.jsonl`, `data/test_track_a.jsonl`을 로컬 캐시에 다운로드했습니다.
- 세 파일의 크기와 SHA-256을 고정 기준과 대조했습니다. 원본 레코드·정답·분할은 수정하지 않았습니다.
- A 담당 다운로드·검증 코드와 합성 테스트를 작성하고, 원문을 포함하지 않는 통계·오류 보고서를 만들었습니다.
- 학습용 5,000건 표본 선정, 데이터 변환, 모델 추가 학습은 아직 수행하지 않았습니다.
- 실제 검사 결과와 중복 관련 검토 사항은 [2단계 보고서](../reports/data-audit-summary.md)에 기록했습니다.
