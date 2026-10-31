# 검사 API의 제한된 클라이언트 측 프로파일링

`profile_detector.py`는 기본적으로 **계획만 출력하며 네트워크를 사용하지 않는다**. 실제 합성 요청은 `--run`을 명시한 경우에만 보낸다. 아직 L4에서 실행하지 않았으며, 아래 도구와 합성 테스트 자체는 GPU 성능 증거가 아니다.

설정은 [배포 HTTPS 검증](DEPLOYED_VERIFICATION.md)과 같은 `.private` JSON을 사용한다. GPU 역할, 정확한 모델/임계값 버전, HTTPS origin 및 필요한 사설 CA를 지정한다. `.private` 디렉터리 0700·설정 파일 0600을 요구한다. TLS 검증·호스트명 검증·`trust_env=False`를 유지하고 프록시·리다이렉트·재시도·OpenAI 호출은 사용하지 않는다. 일반 서버 계약은 이 도구의 측정 대상이 아니다.

작업 디렉터리 `dev`에서 계획을 먼저 확인한다. 출력 파일은 새 경로여야 한다.

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m integration.profile_detector .private/deployed-services.local.json --output .cache/profile-plan-01.json
```

기본 계획은 2,000/16,000코드포인트 × 동시성 1/2/4 × 각 5개 표본이다. 크기별 예열 요청 2개는 측정 표본에서 제외하고 별도로 보관한다. health/ready GET 2회를 포함한 요청 수는 최대 36회다. 계획 JSON은 요청 수, 최대 동시 요청, 각 요청 10초 제한의 합산 상한과 전체 실행 예산을 표시한다.

2,000자·동시성 1 조건의 표본을 더 모으는 명시적 실행 예시:

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m integration.profile_detector .private/deployed-services.local.json --run --size 2000 --concurrency 1 --samples 20 --warmups 2 --budget-seconds 120 --output .cache/profile-run-01.json
```

- `--size`: 2000 또는 16000. 반복 지정할 수 있으며 중복은 거절한다.
- `--concurrency`: 1, 2 또는 4. 반복 지정할 수 있으며 한 번에 최대 4개 요청만 실행한다. 100개 표본 전체를 미리 task로 생성하지 않는다.
- `--samples`: 시나리오별 1–100개. `--warmups`: 크기별 0–10개, 기본 2개다.
- `--budget-seconds`: 0 초과 120 이하. 하나의 절대 deadline이 readiness·예열·전체 시나리오를 공유한다. 남은 요청을 다시 보내거나 예산을 늘리지 않는다. 만료 시 진행 중 요청을 취소하고 task 종료를 기다린 뒤 결과를 기록한다. 동기 응답 처리로 deadline을 넘긴 결과도 성공 표본으로 수락하지 않는다. 클라이언트 생성·정리 비용이나 협조적 취소 완료 시간까지 하드 실시간 상한으로 보장하는 것은 아니다.

원문은 고정 패턴 `공개 예시 public demonstration é 👩‍💻 12345. `를 반복하여 정확히 2,000/16,000개 Python Unicode 코드포인트로 생성한다. 나머지 길이는 패턴 prefix로 구성한다. 실제 사용자 원문을 수집·정규화·잘라내는 작업은 없다. 검사 요청은 기존 `InspectionClient`를 사용하며 **서비스의 10초 제한을 변경하지 않는다**.

측정값은 각 요청의 클라이언트 측 경과 시간이다. 응답의 서버 timing은 별도 필드다. 서로 대체하지 않는다. `first_request`는 첫 검사 요청을 별도로 가리키며, 해당 요청은 예열 또는 측정 목록에도 한 번 기록된다. 이를 독립 표본으로 다시 합산하지 않는다. 서버 재시작·모델 로딩을 수행하지 않으므로 **첫 요청 지연은 cold-start 측정값이 아니다**. 실제 startup/load/warmup 비용은 서버 측 instrumentation으로 따로 측정해야 한다.

완료된 요청의 p50/p95는 **nearest-rank** 방식, 즉 정렬된 값의 `ceil(p × n) - 1` 인덱스로 계산한다. 실패·불완전·timeout·취소 요청을 정상 표본으로 바꾸지 않는다. 각 상태와 시작하지 못한 요청 수도 증거에 남긴다. 완료 표본의 percentile만 보고 전체 요청이 성공했다고 해석하지 않는다.

검증된 complete 응답은 `server_timing_breakdown_ms`에 기존 공개 구간
`queue`, `inference`, `rules`, `total`을 보존한다. 시나리오별
`server_stage_timing_ms`는 각 구간의 `sample_count`, `p50_ms`, `p95_ms`를
nearest-rank로 계산한다. 예열은 별도 요청 기록에 남기며 이 집계에 넣지 않는다.
실패·incomplete·timeout·취소·미실행 요청도 구간 집계에 넣지 않는다. 따라서
클라이언트 전체 결과의 failure/not_started 개수와 함께 해석해야 한다.

구간 표본이 없으면 count는 0, percentile은 null이다. 누락·추가 구간, 음수,
NaN/무한대, bool/문자열 등 잘못된 응답 값은 검사 실패로 처리하며 0ms로
채우지 않는다. 요약 함수에 전달된 complete 기록 자체에 유효한 구간이 없으면
`server_timing_excluded_complete_count`로 표시하고, 해당 조건의 관찰 목표 판정도
`invalid_server_timing_evidence`로 막는다. 기존 실패·미실행 우선 판정은 유지한다.

서버 구간 percentile은 정상 완료 표본에 한정된 설명 통계이며 클라이언트 지연
또는 별도의 구간 SLO 판정이 아니다. 구간 값·percentile을 합산해 전체 시간을
재구성하지 않는다. `inference`는 기존 API의 백엔드 대기 전체 구간이며 순수 GPU
forward 시간이 아니다. opt-in 내부 세부 계측의 preprocessing/model_call 등은
공개 API에 추가하지 않았으므로 이 도구의 원격 집계 대상이 아니다.

2,000자·동시성 1에만 관찰 표본의 p95 ≤ 500ms 판정을 제공한다. 다음 조건을 모두 만족해야 한다.

1. 해당 크기의 예열을 하나 이상 계획하고 모두 complete로 완료했다.
2. 해당 시나리오에 complete 표본이 20개 이상이다.
3. 실패·불완전·timeout·취소·미실행 표본이 없다.

그 외에는 `not_warmed`, `insufficient_samples`, `failed_requests_present` 등을 표시한다. 16,000자와 동시성 2/4는 `descriptive_only`로 기록한다. `met_in_observed_sample`도 반복 측정·환경 통제·독립 표본을 통한 안정적 SLO 검증을 뜻하지 않으며, `stable_slo_established`는 항상 false다. GPU 장치 배치·한국어 탐지 품질도 이 측정만으로 판정하지 않는다.

증거에는 인증 토큰·URL·입력 원문·finding 본문을 저장하지 않는다. 완료 상태, 개수, 버전, 경과 시간과 정제된 오류 코드만 기록한다. 모든 계획 요청이 성공하면 종료 코드 0, 실패나 예산에 의한 미완료가 있으면 1이다. 계획 모드의 0은 실제 서버 실행 성공이 아니다. 출력 파일이 이미 존재하면 요청을 보내기 전에 거절한다.

합성 검증:

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest integration/tests/test_detector_profile.py -q
```

HTTPX mock transport로 동시성 상한, 취소 후 task 정리, 전체 예산, 실패 기록, 모델 버전 drift, 표본/percentile 조건, 기본 네트워크 차단 및 오류 출력의 비밀값 제거를 검증한다. 실제 GPU·실제 서비스에 프로파일 요청을 보낸 결과는 별도 증거가 필요하다.
