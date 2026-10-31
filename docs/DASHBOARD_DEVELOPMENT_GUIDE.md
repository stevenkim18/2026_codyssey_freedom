# C 담당 대시보드 개발·사건 데이터 가이드

이 문서는 C가 사건 수집·조회·화면 개발을 시작할 수 있도록 현재 B 구현과 화면 설계 제안을 연결한다. 함께 제공하는 [합성 데이터](dashboard_example_dataset.log)는 **B 로컬 저널 메타데이터를 NDJSON으로 재현한 개발용 fixture**다. 실제 사용자 로그, 실행 결과 덤프, C가 채택한 HTTP 요청 본문이 아니다.

확인 기준: 2026-10-31 저장소. C의 사건 수집·DB·조회·SSE 구현은 현재 저장소에서 확인되지 않았다. 아래 API 경로는 PRD의 요구사항이며, wire envelope·ACK JSON·인증 배치·조회 응답은 C와 합의할 항목이다. B의 기본 전송 codec은 없고 명시적으로 주입해야 한다. 이 문서로 공용 계약을 확정하거나 C 구현·배포 완료를 선언하지 않는다.

## 1. 먼저 읽을 파일과 작업 경계

| 순서 | 파일 | 용도 |
|---|---|---|
| 1 | [PRD §7·§9·§11](AI_PII_Gateway_PRD_v1.md) | 사건 필드, 세 화면, 보안, T08~T12 완료 기준 |
| 2 | 이 가이드 → [합성 데이터](dashboard_example_dataset.log) | UI와 집계를 독립 개발할 입력·기대값 |
| 3 | [journal.py](../dev/gateway/journal.py), [engine.py](../dev/gateway/engine.py) | 현재 B 필드 검증과 사건 생성 시점 |
| 4 | [delivery.py](../dev/gateway/delivery.py), [event_transport.py](../dev/gateway/event_transport.py), [REPLAY.md](../dev/gateway/REPLAY.md) | 영속 ACK, 부분 승인, 재전송, codec 접점 |
| 5 | [test_event_pipeline.py](../dev/gateway/tests/test_event_pipeline.py), [test_delivery.py](../dev/gateway/tests/test_delivery.py) | 합성 어댑터 기반 통합·장애 사례 |
| 6 | [verify_dashboard_example.py](../dev/integration/verify_dashboard_example.py), [fixture 회귀 테스트](../dev/integration/tests/test_dashboard_example_contract.py) | 실제 B Journal·shipper 왕복, 고정 집계와 안전 입력 검증 |
| 7 | [계약 소유권](../dev/contracts/README.md), [일반 서버 준비 문서](../dev/deployment/GENERAL_SERVER.md) | B/C 소유권과 런타임 준비·C 배포의 구분 |

C는 `dev/observability/`의 사건 계약·수집 API·SQLite·조회·SSE·화면·접근 통제·보관을 담당한다. B는 로컬 저널·재전송·검사와 전달 통제를 담당한다. C가 B의 로컬 저널 파일에 직접 접근하거나 검사·외부 LLM 요청을 재실행하는 구조는 사용하지 않는다. C 계약 채택 시 버전·B/C 계약 테스트와 함께 B 어댑터를 연결한다. ([PRD §3·§7·§9](AI_PII_Gateway_PRD_v1.md), [계약 소유권](../dev/contracts/README.md))

## 2. 현재 B 사건 메타데이터

파일은 UTF-8이며 한 줄이 JSON 객체 하나다. 주석·헤더·빈 줄·외곽 배열은 없다. 실제 B 저널 저장소는 SQLite이며 이 `.log`는 그 데이터베이스의 파일 형식이나 운영 로그 포맷을 바꾸지 않는다. 필드는 아래 18개만 사용한다. 현재 검증기는 추가 필드도 거절한다. (`journal.py:27,43`, `engine.py:48`)

| 필드 | 현재 형식·해석 |
|---|---|
| `schema_version` | 문자열 `1.0`; C wire 버전 합의를 대신하지 않음 |
| `event_id` | 사건 레코드 식별자. 동일 ID·동일 payload는 중복, 다른 payload는 충돌 |
| `client_id`, `session_id` | 클라이언트·세션 상관 식별자. 로그인 사용자·권한 주체로 신뢰하면 안 됨 |
| `request_id`, `inspection_id` | 한 요청의 양방향·단계 연결 / 검사 상관 식별자 |
| `revision` | 양의 정수. 현재 B 생성값은 항상 1이며 전달 단계 순번이 아님 |
| `occurred_at` | UTC ISO 8601. 화면에서 현지 시각 변환 가능, 원본 UTC 유지 |
| `direction` | `outbound`: 외부 공급자로 가는 요청 / `inbound`: CLI에 보여줄 응답 |
| `stage` | `decision` 또는 `delivery`; 각각 별도 `event_id` |
| `decision` | `allow`, `mask`, `block`, `error`; 정책 결과이며 전달 성공 여부와 별개 |
| `delivery_status` | `not_sent`, `unknown`, `forwarded_original`, `forwarded_sanitized`, `delivered` |
| `findings` | `[{"type": "PERSON", "count": 1}]`처럼 유형·횟수만; 원문값·위치·점수 없음 |
| `rule_ids` | 허용된 규칙 식별자 목록; 화면에서 텍스트로만 표시 |
| `model_version`, `policy_version` | 판단 시 기록한 버전; `unknown`은 모델 준비 완료를 뜻하지 않음 |
| `timing_ms` | 0 이상의 유한 수. 현재 B에서는 요청 시작부터 사건 기록까지 누적 경과 ms |
| `error_code` | `null` 또는 허용된 대문자 코드. 외부 오류 본문·예외 문자열 없음 |

`decision/not_sent`는 **그 결정 시점**의 상태다. B는 허용된 outbound 전송 직전에 `delivery/unknown`을 먼저 영속 기록하고, 공급자 응답을 받은 뒤 `forwarded_*` 사건을 추가한다. inbound의 `delivered`는 안전 응답 전달을 기록한 단계이며, 이 상태를 중앙 사건 서버의 ACK와 혼동하면 안 된다. 차단은 `block/not_sent`로 끝날 수 있다. (`engine.py:110,135,148,165`)

같은 요청의 outbound/inbound는 `request_id`로 연결하고 각 검사는 `inspection_id`로 구별한다. 단계 추가에는 새 `event_id`를 쓰지만 사건 재전송은 기존 ID와 payload를 유지한다. PRD는 같은 검사 재시도의 `inspection_id` 유지를 요구한다. 마스킹 재검사의 별도 호출 수를 이 저널 사건 수만으로 복원할 수는 없다.

형식 경계는 **SQLite 내부 행(`event_id`, JSON `payload`) → `Journal.pending()`의 사건 dict → 이 파일의 한 줄당 사건 dict → C 합의 codec 입력 → C 조회·화면 파생 모델** 순서다. 샘플은 `payload` 래퍼나 DB 행을 내보낸 파일이 아니다. `EventHTTPTransport`가 codec에 넘기는 리스트의 각 dict와 같은 B DTO이며, codec의 HTTP envelope와 C 조회 응답 형식은 별도다. 아래 검증기는 실제 Journal에서 읽은 dict를 JSON으로 직렬화·역직렬화해 키와 값의 타입이 그대로 유지되는지 확인한다. **실제 C 화면이 현재 파일을 바로 읽는다는 검증은 아직 없다.**

샘플의 식별자는 모두 고정 합성 UUID다. 운영에서도 식별자·규칙·버전 문자열에 사람 이름·계정명·경로·비밀정보를 끼워 넣지 않는다. 형식 검증만으로 문자열의 민감성까지 판정할 수 없으므로 생성 지점의 허용목록도 유지한다.

## 3. 세 화면과 파생 데이터 제안

다음 `view_*` 이름은 **C 내부 조회·UI read model 제안**이다. 합성 NDJSON이나 B DTO에 필드를 추가하지 않는다. API 응답 채택 여부는 C가 결정한다.

| 화면 | 기본 구성 | 파생값·상태 제안 |
|---|---|---|
| 사건 목록 | 시각, 방향, 유형 요약, 결정, 전달 상태, 필터·페이지 조회 | `view_case_key=(request_id,direction)`, `view_warning`, `view_delivery_state` |
| 사건 상세 | 유형·횟수·규칙·버전·경과시간·안전 오류 코드, 연결된 모든 단계 | `event_id`로 원본 메타데이터 조회; 같은 case의 타임라인을 별도로 연결 |
| 운영·평가 | 방향별 결정 수, 전달 불명 수, 연결 상태, 버전, 지연 | 수집 상태·지연과 모델 정확도 평가를 별도 구역으로 표시 |

기본 사건 수는 `(request_id,direction)`의 수이고 요청 수는 `request_id`의 수다. 저장 레코드 수, 수신 시도 수, 탐지 횟수는 서로 다르다. `event_id` 중복 제거 후에도 decision과 delivery에 같은 findings가 반복되므로 탐지 합계에는 **판단의 대표 레코드만** 사용한다. 이 fixture는 case마다 decision이 정확히 하나다. 운영에서 같은 case에 여러 decision이 오면 모두 보존하고 대표 판단·집계 규칙을 B/C가 정한다. `revision=1`이나 도착 순서로 최신 판단을 추측하지 않는다.

`view_severity`가 필요하면 UI 규칙과 버전을 명시한다. 예: `error`는 운영 실패, `block`은 차단, `mask`는 정제, 허용 유형만 있으면 경고, 무탐지 allow는 정보. 이는 새로운 모델 위험 점수나 유출 판정이 아니다. `ORGANIZATION`, `URL`, `IP_ADDRESS`, `NRP`는 현재 정책에서 경고 후 허용되는 유형이다. URL 탐지 1건을 개인정보 유출·예방한 공격 1건으로 표시하지 않는다. ([PRD §5·§9](AI_PII_Gateway_PRD_v1.md), [policy.py](../dev/gateway/policy.py))

현재 `timing_ms`는 검사 API의 단일 추론 시간과 다르다. inbound 값에는 외부 응답 대기까지 포함될 수 있고 같은 요청의 단계별 누적값을 합치면 중복 계산된다. 시연용 p50/p95에는 방향·단계·표본 수·집계법을 표시한다. 검사 지연 목표는 별도 검사 계측으로, 대시보드 반영 목표는 **중앙 수신→화면 반영** 계측으로 검증한다. 샘플의 숫자는 성능 근거가 아니다.

## 4. 합성 데이터 시나리오와 기대값

ID 표기는 UUID의 마지막 12자리 정수 기준이다. `R1`은 `request_id` 접두부 `20000000`·끝자리 `000000000001`, `E1`은 `event_id` 접두부 `10000000`·같은 끝자리다. 파일의 줄 번호는 수신 순서 fixture이며 생성 순서를 뜻하지 않는다.

| 요청 | 고유 사건 ID | 시나리오·확인할 화면 |
|---|---|---|
| R1 | E1~E5 | 정상 양방향 allow, outbound forwarded_original, inbound delivered |
| R2 | E6~E10 | 요청 mask: EMAIL_ADDRESS 2 + PERSON 1, 정제 전달 후 정상 응답 |
| R3 | E11 | 요청 API_KEY 유형 차단. 비밀값 없이 block/not_sent |
| R4 | E12~E15 | 요청은 전달됐지만 응답 CREDIT_CARD 유형 차단. 양방향을 분리 표시 |
| R5 | E16~E20 | 응답 KR_PHONE_NUMBER 1 마스킹 후 delivered |
| R6 | E21 | 검사 timeout을 가정한 fixture: error/INSPECTION_FAILED. 실제 코드만으로 timeout 원인을 확정할 수 없음 |
| R7 | E22 | incomplete 검사를 가정한 fixture: error/INSPECTION_INVALID. 정상 무탐지로 집계 금지; 부분 findings 없음 |
| R8 | E23~E24 | 전송 중 취소를 가정: allow 뒤 unknown만 남음. 취소 전용 사건이 없어 UI에서 취소 확정 금지 |
| R9 | E25~E29 | URL 1 경고 후 요청 허용, 응답 전달 불명. E29가 E28보다 먼저 도착하는 역순 입력 |
| R10 | E30 | 미지원 요청 형식: error/UNSUPPORTED_PROTOCOL |

E31 이후의 새 ID는 없다. 파일 31~35행은 E1~E5 재수신, 36~38행은 E3~E5 재수신이다. 합성 도착 순서는 중복 제거·순서 뒤바뀜 UI 테스트용이다. 단일 B worker의 배치 스케줄 전체를 기록한 전송 추적은 아니다.

| 검증 항목 | 기대값 |
|---|---:|
| NDJSON 행 / 고유 `event_id` / 재수신 중복 행 | 38 / 30 / 8 |
| 요청 / `(request_id,direction)` 사건 | 10 / 15 |
| 고유 decision / delivery 레코드 | 15 / 15 |
| decision별 allow / mask / block / error | 8 / 2 / 2 / 3 |
| outbound decision: allow / mask / block / error | 5 / 1 / 1 / 3 |
| inbound decision: allow / mask / block / error | 3 / 1 / 1 / 0 |
| 대표 decision의 탐지 횟수 합계 | 7 |
| 유형별 횟수 | EMAIL_ADDRESS 2, PERSON 1, API_KEY 1, CREDIT_CARD 1, KR_PHONE_NUMBER 1, URL 1 |
| 전달 이력 레코드: unknown / forwarded_original / forwarded_sanitized / delivered | 7 / 4 / 1 / 3 |
| 확인된 전달 단계 없이 unknown으로 끝나는 case | 2: R8 outbound, R9 inbound |

unknown 이력 7건 중 5건은 같은 case에 후속 `forwarded_*`가 있다. 7건 모두 현재 전송 장애로 세면 안 된다. 반대로 확인된 전달 단계가 없는 2건을 성공·차단·미전송으로 단정할 수 없다. `occurred_at`은 발생 시각이고 서버 수신 시각은 샘플에 없으므로 수집 지연·SSE 지연·중앙 연결 상태는 이 파일에서 계산할 수 없다.

현재 관측 한계도 UI에 반영한다. `CancelledError`는 검사 중 전용 오류 사건을 남기지 않고 전파될 수 있고, 용량 초과·저널 실패 등도 중앙 사건으로 반드시 남는다고 보장하지 않는다. 로그에 없는 요청을 모두 안전 처리로 간주하지 않는다. timeout/incomplete/cancel의 구체적 원인 표시는 별도 안전한 상태 계약이 생기기 전까지 ‘실패’ 또는 ‘전달 불명’ 수준으로 유지한다. (`engine.py:64,83,115,144`, [취소 회귀 테스트](../dev/gateway/tests/test_engine.py))

## 5. 수집·ACK·SSE 연결 원칙

PRD 경로는 `POST /v1/events/batch`, `GET /v1/events`, `GET /v1/events/{event_id}`, `GET /v1/summary`, `GET /v1/events/stream`이다. 이 문서는 batch envelope나 receipt JSON 예시를 정하지 않는다. `BatchReceipt(acknowledged_ids=...)`는 **B 프로세스 내부 Python 타입**이며 그대로 HTTP 계약이 되는 것은 아니다. ([PRD §7](AI_PII_Gateway_PRD_v1.md), [delivery.py](../dev/gateway/delivery.py), [event_transport.py](../dev/gateway/event_transport.py))

1. C는 입력을 검증하고 인증 주체의 허용된 client 범위를 확인한 뒤 DB에 영속 저장한다. `event_id` 고유 제약과 동일 payload 재수신 처리를 한 쓰기 경로에서 보장한다. 같은 ID의 내용이 다르면 덮어쓰지 않고 안전한 충돌 처리를 한다.
2. ACK는 **DB commit 후** 저장이 확인된 ID에만 보낸다. HTTP 2xx나 메모리 큐 적재만으로 ACK하지 않는다. B는 확인 전 재전송할 수 있으므로 at-least-once 수신을 전제로 한다.
3. 실습 A: E1~E5를 저장한 뒤 응답 유실 → 같은 5개를 다시 입력해도 DB 5행. 실습 B: 같은 배치에 E1·E2만 ACK → B pending은 E3~E5 → 나머지 재전송·ACK 후 pending 0. 잘못된 ID·중복 ACK·부분 ACK는 별도로 검증한다. 실제 B는 보낸 배치 밖 ID나 중복 ID receipt를 거절한다.
4. 수집 재시도는 사건만 재전송한다. 외부 LLM 요청을 재전송하지 않는다. 재생으로 수신 횟수가 늘어도 UI 사건·탐지 수는 늘지 않아야 한다.
5. SSE cursor는 C가 부여하는 증가 순서다. `event_id`·UUID 정렬·UTC 시각·`revision`을 cursor로 쓰지 않는다. snapshot과 SSE의 경계 cursor를 맞추고, 재접속 중복을 제거하며, 보관 범위를 벗어난 cursor는 snapshot 재조회로 복구한다. 느린 소비자에 무한 큐를 두지 않는다.

조회·집계·SSE에는 동일한 권한 범위와 필터 의미를 적용한다. UTC 시간 범위는 예를 들어 `[from,to)`로 정의하고, 결정·방향·유형·버전 필터의 AND/OR 의미와 안정적인 페이지 cursor를 C 계약에서 확정한다. 역순·동시 도착에 타임라인을 보존하고, 전달 결과가 decision보다 먼저 보이면 ‘판단 수신 대기’처럼 불완전 상태를 표시한다. 표본에 없는 순서 보장이나 실시간 연결을 가정하지 않는다.

## 6. 보안·보관·운영 화면

- 원문 프롬프트·응답·탐지값·원문 해시·인증 헤더·자격증명·원시 예외·실제 호스트 정보를 사건·브라우저 콘솔·추적·다운로드에 넣지 않는다. 상세도 `[PERSON] × 1` 같은 유형 자리표시자를 사용한다.
- 모든 문자열은 텍스트 노드나 프레임워크의 기본 escaping으로 렌더링한다. `innerHTML`·HTML 실행·이벤트 문자열 실행을 사용하지 않는다. 유형 URL을 클릭 가능한 사용자 제공 링크로 복원하지 않는다. 링크가 필요하면 고정 허용목록만 쓴다. 악성 HTML 검증 입력은 별도 보안 테스트로 전달하고 운영 DTO에 억지로 추가하지 않는다.
- 서비스 간 수집 자격과 대시보드 로그인 권한을 분리한다. 조회자·운영자 등의 역할 분리는 제안이며 실제 정책은 C가 확정한다. `client_id`나 URL 필터만 바꿔 다른 소유자의 목록·상세·집계·SSE를 볼 수 없게 서버에서 검사한다. TLS와 안전한 SSE 인증을 사용하고 URL query에 인증값을 싣지 않는다.
- 중앙 7일 보관은 PRD의 제안 기준이다. 시간 기준·삭제 주기·백업·내보내기·cursor 만료를 함께 정한다. B의 미확인 저널을 중앙 보관 정책으로 삭제하지 않는다. C SQLite는 PRD의 단일 쓰기·WAL·인덱스·페이지 조회를 검토하고 화면마다 전체 원장을 스캔하지 않는다.
- 화면 연결, 중앙 수집, 검사 서버 readiness, 모델 버전을 구분한다. SSE 연결 성공은 검사 서버 준비 완료가 아니고, 사건이 조용한 상태는 연결 실패 증거가 아니다. 미수신·미계측은 0보다 ‘확인 불가/미측정’으로 표시한다.
- 운영 탐지 수는 모델 정확도·미탐률·유출 방지율이 아니다. 오프라인 품질은 A의 검증 데이터셋·분할·모델·threshold 버전과 함께 별도 표시한다. fixture 재생은 UI·관측 경로 확인이며 실제 탐지 품질·보안 완전성의 증거가 아니다.

근거: [PRD §9·§10·T10](AI_PII_Gateway_PRD_v1.md). 보관·역할·조회 응답·SSE 제어 메시지의 세부 형식은 아직 구현 확인 대상이다.

## 7. 로컬 fixture 검증과 C 테스트 목록

실행 전 `PII_PY312_PYTHON` 환경변수에 **실제 Anaconda `py312`의 Python 실행 파일 경로**를 지정한다. PATH의 `conda`나 환경 이름만 믿으면 다른 배포판이 선택될 수 있다. 공개 문서에는 개인 홈 경로를 고정하지 않는다. 아래는 저장소 루트에서 실행한다.

```sh
rtk proxy "${PII_PY312_PYTHON:?Anaconda py312 Python 경로 필요}" dev/integration/verify_dashboard_example.py
rtk proxy "${PII_PY312_PYTHON:?Anaconda py312 Python 경로 필요}" -m pytest dev/integration/tests/test_dashboard_example_contract.py -q
```

검증기는 입력 파일을 읽기만 하고 임시 디렉터리에만 실제 B Journal을 만든다. 38→30 중복 제거, 타입·키 보존 직렬화 왕복, 동일 ID 내용 충돌 거절, 실제 shipper의 응답 유실·부분/전체 ACK를 합성 어댑터로 확인한다. 네트워크·GPU·C 서버는 사용하지 않는다. 아래 간단한 파서는 표준 라이브러리와 현재 B 검증기로 집계만 확인하며 DB도 만들지 않는다.

```sh
rtk proxy "${PII_PY312_PYTHON:?Anaconda py312 Python 경로 필요}" - <<'PY'
from collections import Counter
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path("dev").resolve()))
from gateway.journal import _validate_event

def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, "duplicate JSON key"
        result[key] = value
    return result

lines = Path("docs/dashboard_example_dataset.log").read_text(encoding="utf-8").splitlines()
rows = [json.loads(line, object_pairs_hook=unique_keys) for line in lines]
unique = {}
for row in rows:
    _validate_event(row)
    identity = row["event_id"]
    assert identity not in unique or unique[identity] == row, "ID conflict"
    unique[identity] = row
decisions = [row for row in unique.values() if row["stage"] == "decision"]
cases = {(row["request_id"], row["direction"]) for row in decisions}
assert (len(rows), len(unique), len(rows)-len(unique)) == (38, 30, 8)
assert len({row["request_id"] for row in unique.values()}) == 10
assert len(cases) == len(decisions) == 15
assert Counter(row["decision"] for row in decisions) == dict(allow=8, mask=2, block=2, error=3)
findings = Counter()
for row in decisions:
    for item in row["findings"]:
        findings[item["type"]] += item["count"]
assert findings == dict(EMAIL_ADDRESS=2, PERSON=1, API_KEY=1, CREDIT_CARD=1, KR_PHONE_NUMBER=1, URL=1)
assert rows[27]["occurred_at"] > rows[28]["occurred_at"]  # intentionally reversed
assert all(row["revision"] == 1 for row in rows)
print("OK: 38 rows, 30 unique events, 15 cases, 7 findings; duplicates and reverse arrival preserved")
PY
```

| 테스트 | C 개발 시 확인할 완료 조건 | PRD |
|---|---|---|
| fixture 수집·집계 | 위 기대값 일치; 38행을 두 번 넣어도 30 고유행·15 case·탐지 7 유지 | T08/T09 |
| 부분 ACK·응답 유실·재시작 | commit 전 ACK 0; 부분 승인만 제거; 미확인 ID 복구; ID 충돌 안전 처리 | T08 |
| 필터·목록·상세·집계 | 같은 권한·시간·유형 조건의 합계 일치; 안정 페이지 경계; unknown 이력과 현재 미확정 분리 | T09 |
| SSE·역순 수신 | E29→E28 역순에서 단계 보존; 재접속 중복 0; 오래된 cursor snapshot 복구; 느린 소비자 제한 | T09 |
| 인증·소유권·XSS | 미인증·타 사용자 상세/집계/SSE 거절; HTML 비실행; 예외·로그·콘솔에 민감값 없음 | T10 |
| 보관·부하·반영 지연 | 만료·백업·cursor 일관성; 수신→화면 p95와 표본 수 계측; 목표 ≤1초를 실제로 검증 | T10/T12 |

C 구현은 위 항목별로 실패 테스트 → 최소 구현 → 통과 확인 → 리팩터링 순서로 진행한다. 현 문서·데이터 확인은 C 서버, 실제 TLS·인증, 브라우저 화면, SSE 복구, 보관 삭제, p95 목표를 검증한 결과가 아니다. 현재 B의 합성 중앙 서버 테스트와 C 실제 구현 검증도 별개다.

인계 시 확정할 항목은 C 소유 wire/ACK와 오류 응답, 자격증명 발급·소유자 매핑, 조회·집계의 대표 판단 규칙, SSE cursor/snapshot 복구 형식, 보관 정책이다. 이 합의 전에도 이 fixture로 세 화면과 로컬 수집·집계 테스트를 개발할 수 있다.
