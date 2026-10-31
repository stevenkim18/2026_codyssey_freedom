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

## 3단계에서 한 변경

- 고정 backbone 리비전의 `spm.model`을 내려받아 SHA-256을 검증했습니다. `config.json`, `tokenizer_config.json`은 검증한 원본 사본을 그대로 사용했습니다.
- 토크나이저 로딩 때 한도 512와 GLiNER 특수 토큰 순서를 지정했습니다. 원본 파일의 내용을 덮어쓰지 않았습니다.
- A 담당 문자→단어 정답 변환, 원문 역변환 정보, 전체 문서 거절·집계 프로그램을 작성했습니다. 기존 B의 계획 함수를 재사용했고 B 운영 코드는 변경하지 않았습니다.
- 실제 신경망 가중치를 내려받거나 모델을 학습하지 않았습니다. 정식 학습 자료가 아닌 진단용 변환 예시를 Git 제외 캐시에 만들었습니다.
- 원본 데이터 레코드·정답·분할은 변경하지 않았고 변환 실패 사유를 기록했습니다. 한국어 경계 호환성 문제를 해결하기 전 이 부분집합을 학습 자료로 사용하지 않습니다.
- 로컬 Mac CPU 검증용 별도 환경에 설치한 외부 패키지의 정확한 버전·출처·확인값은 [환경 기록](../metadata/conversion-local-environment.json), [해시 포함 목록](../configs/conversion-macos.lock.txt)에 있습니다. 서버 CUDA 환경의 기록과 구분합니다.

## 4~8단계에서 한 변경 (2026-10-31 ~ 2026-11-01)

- 고정한 K-PII-Bench(`woohyun212/k-pii-bench`, CC BY 4.0, revision `844f92f7beba98fa964f957ba324017e82451701`)의 `train` 분할에서 변환된 5,000건 표본으로 `urchade/gliner_multi_pii-v1`(Apache-2.0)을 미세조정하여 M1(`checkpoint-1262`, 가중치 SHA-256 `ed2546b8b6a721be18a6a9e644fe6fd423016c7a457b5e30521d85c3d86228a2`)을 생성했습니다.
- 7.5단계에서 독립 생성·검토·동결한 서비스 홀드아웃(`service-holdout-v2-r2`, 300건) 및 공식 Test(`test_track_a`, 28,142건)에서 M0와 M1을 공정 평가하고, 8단계에서 B 모델 패키지 계약(`dev/contracts/model-package.schema.json`)에 맞춘 매니페스트([m1-package.manifest.json](../configs/m1-package.manifest.json)) 및 GPU 런타임 인수 검증(`verdict: "replace"`)을 완료했습니다([최종 보고서 v2](../report_v2.md)).
- 모든 공개 문서·JSON·그래프 산출물에는 원본 텍스트와 개인정보 원문을 포함하지 않았으며, 원본 라이선스 사본(`Apache-2.0`, `MIT`, `CC BY 4.0`)은 변경 없이 그대로 보존합니다.
