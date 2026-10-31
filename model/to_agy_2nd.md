# Gemini4 Argon 인수인계 — 7.5단계 남은 평가와 문서 마무리

> **이행 완료 안내(2026-11-01 01:58 KST):** 본 2차 인수인계서의 모든 남은 작업(공식 Test `test_track_a` 28,142건 M0·M1 완주 및 로컬 회수, [`stage7_5-official-final-v1.json`](reports/stage7_5-official-final-v1.json) 산출, 서비스·공식 Test 오류/잔존/정상 실패 원인 전수 분류, [`model/graph/`](graph/README.md) 18종 재생성, [`model/report_v1.md`](report_v1.md) 및 `model/` 내 문서 동기화, `pytest` 통과 검증)을 완료했습니다. 아래 본문은 인수인계 시점의 명세로 보존하며, 완료된 최종 결과는 [`report_v1.md`](report_v1.md)와 [`stage7_5-progress.md`](reports/stage7_5-progress.md)를 확인하세요.

작성일: 2026-11-01 KST · 작업 브랜치: `model` · 다음 작업자: Gemini4 Argon (이행 완료)

## 1. 이 파일을 읽고 바로 알아야 할 것

**서비스 시험 300건과 dev 설정 선택은 완료했습니다. 공식 Test M0는 최초 추론만 완료했고, 정책·재검사 계산 중 사용자 사용 한도 조건에 따라 안전 중단했습니다. 공식 Test M1은 시작하지 않았습니다. 7.5단계 전체 완료나 M1 운영 교체 승인은 아닙니다.**

다음 작업은 새 학습이 아니라 **고정한 설정으로 공식 Test 평가를 끝내고, 서비스 결과의 오류·잔존을 검토한 뒤 최종 보고서를 마무리하는 것**입니다. 원래 사용자는 5분마다 진척 안내, 사용 한도 10% 미만이면 자체 작업 안전 중단, 전체 완료 후 문서 갱신·요약·커밋/푸시 여부 질문을 요청했습니다. 플랫폼 사용량 조회가 불가능하면 남은 양을 추정해서 사실처럼 보고하지 마세요.

코드와 문서는 로컬 작업 트리에 있습니다. **아직 커밋·푸시하지 않았습니다.** 기존 사용자·Agy 변경도 섞여 있으므로 초기화·일괄 복원하지 마세요. 다른 컴퓨터에서 Git만 새로 받으면 이번 작업과 비공개 캐시가 모두 들어 있지 않습니다.

| 읽을 근거 | 용도 |
|---|---|
| [현재 체크포인트](reports/stage7_5-checkpoint-v1.json) | 중단 상태·남은 작업·보존 위치 |
| [보고서 중간 초안](report_v1.md) | 실측 수치·조건·해석·운영 한계 |
| [서비스 최종 집계](reports/stage7_5-service-final-v1.json) | 두 모델의 18유형 TP/FP/FN, 정책 지표, CI, 판정 |
| [dev 선택 결과](reports/stage7_5-selection-v1.json) | 후보별 점수·선택 규칙·선택 가중치·원본 결과 해시 |
| [고정 평가 계획](configs/stage7_5-evaluation-plan.json) | 최종 시험 전에 고정한 탐색 범위·fallback 규칙 |
| [파일 무결성 목록](reports/stage7_5-handoff-integrity-v1.json) | 전달 시점 코드·자료·결과·그래프·원시 예측의 SHA-256 |
| [복구 증거 v2](reports/stage7_5-recovery-v2.json) | 실제 서버 환경·저장본·토크나이저 해시·학습 로그 |
| [창 계획 감사](reports/stage7_5-coverage-audit-v1.json) | 경계 보완 후 dev 동일성·전체 시험 자료 계획 검증 |
| [그래프 목록](graph/README.md) | 현재 11종 PNG/SVG와 재현 방법 |
| [PRD](../docs/AI_PII_Gateway_PRD_v1.md) | 평가·교체·운영 인수 요구사항 |

`stage7_5.md`, `to_codex.md`, `to_agy.md` 본문의 일부는 이전 계획/역사 기록입니다. 현재 실행 상태는 **이 파일과 체크포인트 JSON을 우선**합니다. 외부 모델 카드·라이선스 사본은 고치지 않습니다.

## 2. 끝난 일과 끝나지 않은 일

| 작업 | 실제 상태 |
|---|---|
| 기존 실행 회수 | 소스·환경·학습 기록·5k 표본 분포·저장본 해시 확보. 실행 인자 파일은 없어 모든 override까지 확정 불가 |
| 평가 코드 보완 | A 공통 분할 실제 GLiNER 예측 연결, 미완료/오류 보존, 해시 검사, 비교 조건 ID, B 인수 미확정 처리 |
| GPU 검증 | 네 가중치 새 프로세스 로딩 smoke 및 실제 채점 경로 회귀 검증 완료 |
| 새 5k 준비 | 고유 문장 5,000개·5,058창, 12분야 균형. **학습에는 사용하지 않음** |
| 서비스 r2 | 사용자 피드백 수정 → 300건 재검토 → 사용자 동결 승인 완료 |
| Dev | 공식 dev 28,147건 + 별도 정상 조정 200건 = 28,347건. M0·M1 epoch1·epoch2 모두 완료 |
| 서비스 최종 시험 | 같은 조건에서 M0·M1 모두 300/300건 검사 완료, 정책 두 모드·paired CI 완료 |
| 공식 Test M0 | **완료 (2026-11-01)**: `runs/official-handoff-v1/final-official-m0-v1` 28,142/28,142 추론 및 정책·1회 재검사 완주, 로컬 회수 완료 ([stage7_5-official-final-v1.json](reports/stage7_5-official-final-v1.json)) |
| 공식 Test M1 | **완료 (2026-11-01)**: `runs/official-handoff-v1/final-official-m1-v1` 28,142/28,142 추론 및 정책·1회 재검사 완주, 로컬 회수 완료 ([stage7_5-official-final-v1.json](reports/stage7_5-official-final-v1.json)) |
| 문서·그림 | **완료 (2026-11-01)**: [최종 보고서 v1](report_v1.md), [18종 그래프 PNG/SVG](graph/README.md), 오류·잔존·정상 실패 전수 분류, `model/` 내 문서 동기화 완료 (`pending_runtime_acceptance`) |
| 운영 적용 (8단계) | **미실행 (8단계 B 운영 인수 대기)**: B 분할 채택(`b_splitter_adoption`)·로딩·지연·메모리·통합·재검사(`REINSPECTION_FAILED`) 복구 인수(`runtime_acceptance`) 미확정 |

로컬 검증은 마지막 실행에서 **209 passed**, 변경 공백 검사 통과입니다. 단위 테스트 통과와 실제 모델 성능·B 운영 승인을 혼동하지 않습니다.

## 3. 측정 결과와 해석

### Dev: 선택된 공통 임계값 0.5

| 가중치 | 전체 F1 | 중요 5종 recall | 정상 차단·오류 |
|---|---:|---:|---:|
| M0 | 63.0047% | 74.7093% | 0/200 |
| M1 epoch1 / step631 | 96.3236% | 99.9957% | 10/200 |
| M1 epoch2 / step1262 | 97.7746% | 99.9957% | 10/200 |

같은 임계값 그리드 `0.35, 0.5, 0.65, 0.8`를 세 가중치에 적용했습니다. M1은 모든 임계값에서 정상 10/200건을 차단해 **적격 조합이 없었습니다**. 사전에 정한 fallback 규칙으로 epoch2·0.5를 선택했으며 `dev_eligible=false`입니다. 이를 적격 기준 통과로 바꾸거나 숨기지 마세요. 정상 오류 10건은 계산용 13자리 정수 예제 계열에 집중돼 있었습니다. 임계값만 올리면 자동 해결된다는 근거는 없습니다.

M0의 63%는 공통 한국어 분할 적용 후 결과입니다. 과거 보고의 dev 19.61%와 빼서 학습 향상 폭으로 발표하면 안 됩니다. dev 양성 문서는 합성 템플릿 기반이고 PERSONAL_ATTRIBUTE gold가 없어, 높은 점수가 실제 서비스 일반화를 보장하지 않습니다.

### 사용자 검토·동결 서비스 300건

| 지표 | M0 | M1 | 변화 |
|---|---:|---:|---:|
| 전체 micro F1 | 76.4967% | 89.3816% | +12.8849%p |
| 중요 5종 micro recall | 89.5238% | 94.7619% | +5.2381%p |
| 중요 5종 미탐 | 22/210 | 11/210 | −11 |
| 정상 차단·오류 | 1/100 | 2/100 | +1%p |
| allow / mask / block / error | 98 / 34 / 76 / 92 | 93 / 57 / 93 / 57 | 실제 B 오프라인 경로 |
| 전달 문서 보호 대상 gold 잔존 | 22/54 (40.74%) | 6/79 (7.59%) | 조건부 분모가 다름 |

중요 5종은 주민번호·카드·계좌·전화·이메일입니다. 서비스에서 각 중요 유형 recall의 퇴행은 없지만 생년월일·주소·개인 특성 등 일부 다른 유형은 악화했습니다. 모델 단독과 동일 규칙 결합 모두 수치 게이트 통과, 판정은 **`pending_runtime_acceptance`**입니다. +1%p는 정상 실패 허용 상한에 걸친 값입니다. M0 정상 실패 1건은 재검사 실패, M1 2건은 명시적 차단입니다.

paired document bootstrap 1,000회·seed42의 95% 구간: F1 변화 +9.14~+16.63%p, 중요 recall +2.47~+8.45%p, 정상 실패 증가 −2~+5%p. 합성·템플릿 의존성과 표본 100건의 한계를 적습니다.

**수치 게이트 통과 ≠ 완전 보호 ≠ 운영 준비 완료.** 잔존 분모는 allow/mask로 실제 전달된 문서의 보호 대상 gold만 포함하며, block/error 문서와 정책상 허용 유형(기관명·URL 등)은 제외합니다. 잔존 6건 및 error 57건을 검토 없이 넘기지 마세요. 규칙 결합은 모델 오탐을 제거하는 기능이 아닙니다. 코드의 `runtime_acceptance=None`을 증거 없이 True로 바꾸면 안 됩니다.

## 4. 고정 설정·체크포인트·자료 확인값

**추가 학습하지 말고 다음 가중치와 임계값을 그대로 평가하세요.**

| 모델 | 서버 디렉터리 | 가중치 SHA-256 |
|---|---|---|
| 선택 M0 (`pytorch_model.bin`) | `/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0` | `3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d` |
| 선택 M1 (`model.safetensors`) | `/home1/ncloud/m1_checkpoints/checkpoint-1262` | `ed2546b8b6a721be18a6a9e644fe6fd423016c7a457b5e30521d85c3d86228a2` |
| 미선택 epoch1 (`model.safetensors`) | `/home1/ncloud/m1_checkpoints/checkpoint-631` | `11d9c46e79d9198d55b276c9a511cd3b349717897f9c43eb018484f425c3e763` |
| 별도 저장 M1 final (`pytorch_model.bin`) | `/home1/ncloud/m1_model` | `099ac5d30880b52abaf26d0bbffc6e868e55f1f8b64d1e437ee793f9a5865fba` |

마지막 행은 파일 포맷도 다르고 **현재 선택된 평가 가중치가 아닙니다**. 편하다는 이유로 선택 저장본 대신 쓰지 않습니다. config·토크나이저·manifest 해시는 [복구 v2](reports/stage7_5-recovery-v2.json)에 있고 실행기가 검사합니다.

- 공통 임계값 `0.5`, 분할 `ko_atomic_suffix_v3`, 실제 단어 분할을 모델 로딩마다 재연결.
- max words 384, span 12, 라벨 프롬프트 포함 subtokens 512, 입력 16,000 코드포인트. 기존 라벨 순서·규칙·정책·후처리 고정.
- 동결 r2 SHA-256: `26c21a0293ff71c49be9d36ee9749c3d85249f545713c24dcb49027758f7db87`.
- dev+정상200 SHA-256: `6a60cb889f2641039569a871c3c8ef2f1c45cc70464c7a90f6c5c3aa4a03a1b4`.
- 공식 Test SHA-256: `219c023f69832f9089d59df00368320fc76be81e05ee602cf0909289cd456add`.
- 기존 실제 학습 5k SHA-256: `a1936a858d8cfea593792681e796f5ef50d96c8e9b376745adb732f327030506`.

기존 M1은 5,000문서 중 고유 문장 4,996개·5,048창으로 학습됐습니다. 새 5k 표본 준비와 혼동하지 마세요. 복구 소스 인자는 batch8·누적1·LR3e-5·2 epochs이며 원래 보고의 batch4·누적2·LR2e-5는 확정 근거가 없습니다. trainer 로그는 step50 loss71.1466 → step1250 loss1.2245입니다. 원본 인자 파일 부재를 공개하고 source만으로 모든 실행 override를 단정하지 않습니다.

## 5. 어디에 무엇이 있나요?

로컬 저장소: `/Users/jingeollee/Desktop/codi-modi/2026_codyssey_freedom`.

비공개 로컬 기준 폴더: `model/.cache/stage7_5-20261031/`.

| 상대 위치 | 내용 |
|---|---|
| `dev-recovered-v1/runs/dev-*/result.json` | 완료한 dev 세 실행 집계 |
| `dev-recovered-v1/selection-v1.json` | dev 선택 원본 |
| `holdout-v2-r2-frozen/service-holdout-v2.frozen.jsonl` | 동결 서비스 원문·정답 |
| `holdout-v2-r2/review-decisions.approved.json` | 사용자 승인 기록 |
| `holdout-v2/review-feedback.md` | 최초 승인 보류 피드백. r2 승인으로 해결됐으나 원본 보존 |
| `dev-calibration-v1/dev.calibration.jsonl` | 실제 dev 조정 자료 |
| `train-5k-v1/` | 새 표본, 미학습 |
| `final-recovered-v2/runs/final-v2/final-service-{m0,m1}-v1/` | 서비스 원시 예측·result·record·실행 조건·자원 관측 |
| `final-recovered-v2/runs/final-v2/final-official-m0-v1/` | 공식 M0 원시 예측 28,142건·provenance·run-state·중단 failed.json |
| `final-recovered-v2/runs/final-v2/batch-state.json` | 부모 배치 종료 상태 |
| `dev-evaluation-v1.log`, `final-evaluation-v1.log`, `final-evaluation-v2.log` | 실행·실패·중단 로그 |
| `code-v3.tar.gz`, `code-v4.tar.gz`, `code-v5.tar.gz` | 각 격리 실행 당시 소스 묶음 |

공식 Test 로컬 원문: `model/.cache/k-pii-bench/844f92f7beba98fa964f957ba324017e82451701/data/test_track_a.jsonl`.

| 서버 경로 | 내용 |
|---|---|
| `/home1/ncloud/stage7_5_20261031_codex_v3` | 완료한 dev 실행과 자료. 덮어쓰지 않음 |
| `…_v3/data/test_track_a.jsonl` | 공식 Test 28,142건 |
| `…_v3/data/service-holdout-v2.frozen.jsonl` | 동결 r2 300건 |
| `/home1/ncloud/stage7_5_20261031_codex_v4/selection-v1.json` | 최종 시험 전에 선택한 원본 설정 |
| `…_v4/runs/final-v1/` | 첫 서비스 평가 실패 이력. 유효한 최종 실행 아님 |
| `/home1/ncloud/stage7_5_20261031_codex_v5/runs/final-v2` | 보완 후 서비스 완료·공식 M0 안전 중단 |

`…_v3` 등은 표의 상위 경로 축약입니다. 실행 명령에는 아래의 실제 전체 경로를 씁니다. 캐시·서버가 없으면 원시 예측 재사용과 CI 재계산이 불가능하므로, 없는데 있다고 가정하지 않습니다.

## 6. 접속과 보존 원칙

- 로컬 `model/.secrets`에 접속 정보가 있습니다. **문서·로그·터미널 출력·Git에 비밀번호나 키 내용을 복사하지 않습니다.**
- VPN 동시 접속은 한 사람 제한입니다. 사용자가 연결을 확보한 상태인지 먼저 확인합니다. 이 인계 문서 작성 중에는 재접속하거나 GPU 평가를 재시작하지 않았습니다.
- 마지막 검증 서버는 L4, Python `/home1/ncloud/miniforge3/envs/py312/bin/python`, torch2.10.0 / gliner0.2.29 / transformers4.57.3 / tokenizers0.22.2 / numpy1.26.4입니다. 로컬 CPU 환경과 구분합니다.
- 이전 접속은 `.cache/id_ed25519`, `.cache/gpu_known_hosts`를 썼고 strict host checking을 유지했습니다. 호스트·계정은 secrets와 확인하고 알려진 호스트 키가 달라지면 원인부터 확인합니다. 기존 `ssh_gpu.sh`의 호스트 검증 끄기 설정을 복제하지 않습니다.
- 기존 모델·원본 데이터·서버 Python 환경을 덮어쓰거나 업그레이드하지 않습니다. 다른 팀원 GPU 프로세스를 종료하지 않습니다.
- 중단 시에는 `model/evaluation/stop_run.py --run-dir <자체 실행 디렉터리>`를 해당 서버 작업 루트에서 실행합니다. 이 도구는 PID·명령·작업 경로·UID를 확인하고 자체 실행에만 SIGINT를 보냅니다. `pkill python`을 쓰지 않습니다.

직전 실행은 2026-11-01 00:50 KST경 사용량 9% 조건으로 중단했고 **자체 프로세스 소멸 및 M1 미시작을 확인**했습니다. 이후 서버 상태는 다시 확인해야 합니다. `run-state.json`의 과거 PID/status만 보고 현재 실행 중이라고 판단하지 않습니다.

## 7. 첫 실행 실패와 코드 버전을 알아야 하는 이유

첫 서비스 실행은 40번째 문서에서 한국어 접미사 일부가 창 끝에 걸려 재분할 결과가 달라졌고, 검사기가 `incomplete`로 중단했습니다. 빈 예측으로 성공 처리하지 않았습니다.

`model/segmentation/coverage_backend.py`는 B 원본 계획이 실패할 때만 전체 공백 구분 묶음 경계로 창을 다시 잡습니다. 실제 분할 일치·word/subtoken 한도·겹침·문자 범위를 검사하는 **A 전용 보완**입니다. B 운영 코드는 변경하지 않았습니다. 별도 일반 예제로 실패를 재현하고 세 회귀 테스트를 추가했습니다.

전체 계획 감사 결과: dev 28,347건 기존 계획 그대로, 서비스 300건 중 1건만 보완, 공식 Test 28,142건 기존 계획 그대로였습니다. 모델·임계값·동결 정답·문서는 바꾸지 않았습니다. 첫 실패와 보완 후 실행 이력을 보고서에서 지우지 않습니다.

v3는 dev 당시 코드, v5는 최종 평가 당시 코드입니다. 원격 v5에는 이후 로컬에서 만든 `summarize_final.py`나 최신 그래프 코드가 없을 수 있습니다. 집계·그림은 로컬 최신 코드로 실행하세요. 실행 중 코드를 덮어쓰면 시작 때 기록한 코드 해시와 실제 실행이 달라질 수 있으므로 기존 폴더는 보존합니다.

## 8. 다음 작업 순서와 실행 예

### A. 먼저 무결성과 작업 범위를 확인합니다

1. 저장소 브랜치·작업 트리, 아래 해시 검증, 원시 자료 존재 여부를 확인합니다.
2. [선택 결과](reports/stage7_5-selection-v1.json)의 M0/M1 가중치와 공통 임계값 0.5를 유지합니다.
3. 최종 시험 성적을 보고 임계값·라벨·분할·학습 표본·epoch를 새로 선택하지 않습니다. 새 개선 실험을 하려면 별도 실험과 새 최종 검증 계획이 필요합니다.

저장소 루트에서 실행할 무결성 확인:

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path
m = json.loads(Path('model/reports/stage7_5-handoff-integrity-v1.json').read_text())
for group in ('repository_files', 'private_cache_files'):
    for name, expected in m[group].items():
        p = Path(name)
        if not p.is_file():
            raise SystemExit('Missing file: ' + name)
        h = hashlib.sha256()
        with p.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != expected['sha256']:
            raise SystemExit('Changed file: ' + name)
print('All handoff snapshot hashes match')
PY
```

캐시가 다른 컴퓨터에 없으면 이 검사는 의도적으로 실패합니다. 필요한 원문·예측을 승인된 경로로 복구한 뒤 검사하세요. 이것은 전달 시점 스냅샷으로, 이후 정당한 수정 후에도 해시가 같아야 한다는 뜻은 아닙니다. 변경 이유와 새 확인값을 남깁니다.

### B. 공식 Test M0를 마무리하고 M1을 평가합니다

**현재 `run_final.py`에는 자동 resume이나 저장 예측 재사용 옵션이 없습니다.** 기존 output root를 다시 넘기면 실패합니다. `failed.json`을 지우거나 `result.json`을 만들어서 완료로 가장하지 않습니다. 서비스 300건은 완료했으므로 전체 `run_final.py` 배치를 다시 돌릴 필요가 없습니다.

가능한 방법은 두 가지입니다.

- **코드 변경이 적은 방법:** 아래와 같이 새로운 출력 경로에서 공식 Test M0·M1을 같은 고정 설정으로 순차 실행합니다. M0 최초 추론도 다시 계산되지만 기존 기록은 보존되고 채점 경로를 재구현하지 않습니다.
- **추론 재사용 방법:** 별도 실행기를 먼저 구현·검증합니다. 저장 예측 SHA, 모든 28,142 ID의 유일성·완료 상태·정답 자료 SHA, 가중치/config/tokenizer·임계값·분할·코드 provenance를 확인한 뒤 정책·재검사를 계산해야 합니다. 단순히 예측 파일이 있다는 이유로 재사용하지 않습니다. 새 경로·부모 실행 이력·원시 예측 출처·실제 시간 범위를 기록하고 GPU 재검사까지 완료해야 합니다. 이 기능은 아직 구현하지 않았습니다.

아래 명령은 **인계용 예시로 작성했으며 이번에 실행하지 않았습니다.** 서버 접속 후 bash에서 실행합니다. 새 출력 루트가 이미 있으면 이름을 바꾸고, 기존 산출물은 보존하세요.

```bash
set -euo pipefail
cd /home1/ncloud/stage7_5_20261031_codex_v5
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
EVAL_PYTHON=/home1/ncloud/miniforge3/envs/py312/bin/python
DEV_ROOT=/home1/ncloud/stage7_5_20261031_codex_v3
SELECTION=/home1/ncloud/stage7_5_20261031_codex_v4/selection-v1.json
OUTPUT_ROOT=runs/official-handoff-v1
test ! -e "$OUTPUT_ROOT"
mkdir "$OUTPUT_ROOT"
for ROLE in m0 m1; do
  MODEL_DIR=$("$EVAL_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected"][sys.argv[2]+"_model_dir"])' "$SELECTION" "$ROLE")
  EXTRA_ARGS=()
  if [ "$ROLE" = m1 ]; then
    EXTRA_ARGS=(--baseline-experiment-id final-official-m0-v1)
  fi
  "$EVAL_PYTHON" -m model.evaluation.run_checkpoint \
    --model-dir "$MODEL_DIR" \
    --manifest /home1/ncloud/m1_model/manifest.json \
    --evidence model/reports/stage7_5-recovery-v2.json \
    --output-dir "$OUTPUT_ROOT/final-official-$ROLE-v1" \
    --experiment-id "final-official-$ROLE-v1" \
    --purpose final --device cuda --model-role "$ROLE" \
    --dataset "$DEV_ROOT/data/test_track_a.jsonl" \
    --dataset-version kpii-test-track-a-v1 \
    --dataset-revision 844f92f7beba98fa964f957ba324017e82451701 \
    --holdout-manifest model/evaluation/service-holdout-v2-r2.frozen.manifest.json \
    --holdout-dataset "$DEV_ROOT/data/service-holdout-v2.frozen.jsonl" \
    --splitter ko_atomic_suffix_v3 --threshold 0.5 "${EXTRA_ARGS[@]}"
done
```

`set -e`에 따라 M0 실패 시 M1로 진행하지 않습니다. 각 실행에 complete 28,142건, `result.json`, 두 모드 record가 있는지 확인하세요. 최초 추론 완료 로그만으로 완료 처리하면 안 됩니다. 위 직접 실행 명령은 부모 `run_final.py`의 GPU/RSS 자원 관측을 만들지 않습니다. 운영 지연·메모리 인수가 필요하면 별도로 측정하고, 없는데 측정했다고 보고하지 않습니다.

### C. 로컬 집계·그래프·최종 문서를 완성합니다

공식 두 실행의 디렉터리를 로컬 **새 캐시** `model/.cache/stage7_5-20261031/official-handoff-v1/` 아래에 복사한 경우의 예입니다. 복사 과정은 접속 정보나 원문을 출력하지 않는 도구를 사용합니다.

```bash
model/.cache/conversion-venv/bin/python -m model.evaluation.summarize_final \
  --split official \
  --run-root model/.cache/stage7_5-20261031/official-handoff-v1 \
  --dataset model/.cache/k-pii-bench/844f92f7beba98fa964f957ba324017e82451701/data/test_track_a.jsonl \
  --output model/reports/stage7_5-official-final-v1.json

model/.cache/conversion-venv/bin/python -m pytest model/tests -q
git diff --check
```

집계기는 양쪽 `purpose=final`·성공 상태·자료 SHA·완료 건수·비교 가능 조건을 검사하고 paired CI를 계산합니다. **공식 Test에는 정상 gold 문서가 없으므로 정상 실패율은 N/A**입니다. 공식 결과의 `replacement_decision`이 정상 분모 부재로 pending이어도 오류를 감추려고 0%로 채우지 마세요. 공식 F1·중요 recall·유형별 퇴행과 서비스의 정상 실패 게이트를 함께 검토해 전체 결론을 작성합니다.

그래프 생성기는 하나의 run-root 아래 서비스와 공식 디렉터리를 함께 찾습니다. 기존 서비스 두 디렉터리와 새 공식 두 디렉터리를 **새 집계용 로컬 폴더**에 복사/연결한 뒤 실행하면 원래 증거를 덮어쓰지 않고 모두 그릴 수 있습니다.

```bash
model/.cache/conversion-venv/bin/python -m model.evaluation.build_graphs \
  --dev-root model/.cache/stage7_5-20261031/dev-recovered-v1/runs \
  --final-root model/.cache/stage7_5-20261031/final-combined-v1
```

`final-combined-v1`은 자동 생성되지 않습니다. 그 아래 `final-service-m0-v1`, `final-service-m1-v1`, `final-official-m0-v1`, `final-official-m1-v1`을 준비해야 합니다. 현재 그래프 코드는 서비스 paired CI를 그리지만 공식 paired CI 그림은 자동 생성하지 않습니다. 필요하면 이미 계산한 공식 CI JSON을 쓰는 그림을 추가하고 수치·단위·분모·0 gold의 N/A 처리를 확인하세요.

로컬 CPU Python은 `model/.cache/conversion-venv/bin/python`입니다. Matplotlib3.10.0 설치·pip check 확인 이력이 있습니다. 한국어 글꼴은 macOS Apple SD Gothic Neo를 쓰고 SVG는 글꼴을 경로로 저장합니다. 그림 재생성 후 실제 PNG를 열어 잘림·글꼴·축·분모를 확인합니다. `graph-data.json`에는 집계·수치·입력 SHA만 두고 문서 원문·ID·접속 정보를 넣지 않습니다.

## 9. 마지막에 무엇을 보고해야 하나요?

1. 공식 Test M0/M1 동일 조건 비교, 전체·18유형·중요 유형 TP/FP/FN·분모·변화·CI. 정상 분모 0은 N/A.
2. 서비스 정상 실패가 허용 상한이라는 점, dev 부적격 fallback 선택 이력, 서비스 잔존 6/79 및 error 57/300의 사례 분류. 실패 유형을 추측과 확인으로 나눕니다.
3. 기존 학습 이력 불확실성·새 표본 미학습·합성 시험의 한계·초기 실행 실패와 보완·사용 한도 중단 이력.
4. 모델 단독과 동일 규칙 결합, 서버 내부 시간과 B 클라이언트 p95의 차이. 캐시된 규칙 결합 재검사 시간은 독립 지연 실험이 아닙니다. 자원 관측 최대는 진짜 peak 보장이 아닙니다.
5. 최종 교체 판정과 B 인수 미확정 상태. 근거 없이 `replace` 또는 “8단계 인계 완료”로 바꾸지 않습니다.
6. `report_v1.md`, `graph/README.md`, `model_README.md`, `process.md`, `stage7_5.md`, `to_codex.md`, 이 인계서 및 관련 평가/분할/보고 문서를 실제 상태에 맞게 갱신합니다. 외부 모델 카드·라이선스 원문은 보존합니다.
7. 사용자에게 작업 요약 후 **커밋·푸시할지 묻습니다.** 이번 문서 작성 요청은 커밋·푸시 승인으로 해석하지 않습니다. 원문·원시 예측·모델·키·secrets·캐시는 Git에 넣지 않습니다.

추가 epoch는 현재 남은 작업이 아닙니다. 이후 오차단을 개선하려면 dev의 정상 예시·자료 구성·유형 혼동을 근거로 별도 실험을 설계합니다. 최종 시험을 본 뒤 같은 시험으로 성능을 골라 보고하지 않습니다.
