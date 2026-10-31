# R08 지속 사건 재전송

`ReplayWorker`는 기존 `LocalEventShipper`를 일정에 따라 실행한다. 자동 생성되는
워커나 기본 네트워크 어댑터는 없다. C의 수집 API·인증·receipt 형식이 확정된
뒤 운영자가 해당 계약을 구현한 비동기 어댑터를 명시적으로 주입해야 한다.
현재 테스트 통과는 실제 C 서버 연동 완료를 의미하지 않는다.

`EventHTTPTransport(url, token, codec=approved_codec)`는 인증 HTTPS 전송을 제공한다.
URL은 `/v1/events/batch`이며 codec은 필수다. 기본 C wire/ACK 형식은 없다.
codec의 `encode(events)`는 원문 없는 메타데이터 사본을 JSON으로 변환하고,
`decode(response)`는 합의된 영속 저장 확인을 해석한 `BatchReceipt`를 반환한다.
HTTP 성공만으로 승인하지 않으며 반환 ID의 중복·범위·형식을 다시 검사한다.
서버에는 `transport.send_batch`를 주입한다. HTTP 클라이언트는 TLS/호스트명
검증을 유지하고 환경 프록시·인증·자동 재시도·리다이렉트를 사용하지 않는다.
최대 100건/1MiB·10초 제한을 적용한다. `ca_file`은 명시한 사설 CA이며
검증 해제 옵션은 없다. 테스트용 loopback HTTP/MockTransport는 실제 TLS 증거가 아니다.
transport의 연결 종료는 호출자가 서버 worker 종료 이후 수행한다.

```python
from gateway.delivery import LocalEventShipper
from gateway.replay import ReplayWorker

# journal은 이미 연 영속 Journal, central_adapter는 명시적으로 준비한 어댑터다.
shipper = LocalEventShipper(journal, central_adapter, batch_size=100, send_timeout_s=10)
worker = ReplayWorker(shipper, min_delay_s=1, max_delay_s=60)
await worker.start()
try:
    await application_lifetime()
finally:
    await worker.stop()
# stop 완료 후 journal을 닫는다.
```

워커는 한 번에 한 배치만 요청하고 모든 시도 뒤에 양수 간격을 기다린다.
보낸 사건 중 하나라도 로컬에서 영속 승인되면 대기시간을 최솟값으로 되돌린다.
빈 큐, 송신 실패, 승인 저장 실패, 승인 없는 receipt, `SEND_DRAINING`은
최대 간격까지 지수 백오프한다. 시계가 뒤로 가거나 주입한 sleep이 시간을
진행시키지 않으면 busy loop 대신 `REPLAY_SCHEDULER_FAILED`로 종료한다.

같은 이벤트 루프에서 `start()`를 반복하면 기존 task를 돌려준다. `stop()`은
수명주기 lock을 얻은 시점의 task를 취소하고 종료를 기다린다. 그 뒤 별도로
요청한 `start()`는 새 세대를 시작할 수 있다. `run()`을 직접 await하면 취소 시
자신이 기다리던 세대만 종료한다. 이전 세대의 뒤늦은 종료 처리는 새 세대를
취소하지 않는다. 애플리케이션 수명주기 소유자는 하나로 두는 것을 권장한다.

취소를 무시하는 네트워크 어댑터는 shipper 소유 아래 최대 한 건이 남아
종료될 수 있다. 워커 재시작은 동일 shipper를 사용하여 이 동안 새 송신을
억제한다. 늦은 receipt는 승인으로 받아들이지 않는다. 어댑터의 자체 연결
종료도 해당 운영 어댑터가 관리해야 한다.

워커는 journal을 직접 승인하거나 원문·외부 LLM 요청을 재시도하지 않는다.
실패한 사건은 기존 영속 journal에 남으며 프로세스 재시작 후 같은 ID로
다시 전송된다. 중앙 어댑터는 ID 멱등성과 **영속 저장 확인 후** `BatchReceipt`
반환을 보장해야 한다. 정확한 승인 검증과 로컬 삭제는 기존 shipper가 담당한다.
`last_report`와 `error_code`는 안전한 상태 코드만 제공한다.

```sh
python -m pytest gateway/tests/test_replay.py gateway/tests/test_replay_adversarial.py gateway/tests/test_delivery.py -q
```

검증 범위는 주입한 타이머·로컬 SQLite·합성 중앙 어댑터다. 실제 C 수집 서버,
서버 간 TLS·인증·장기 네트워크 장애 복구는 별도 통합 검증 대상이다.

## 게이트웨이 서버 수명주기 연결

`gateway.server.create_server(settings)`의 기본 동작은 재전송 비활성이다.
`send_events`를 명시적으로 주입한 경우에만 같은 게이트웨이 journal을 사용하는
shipper와 worker를 준비한다. 환경변수로 켜거나 임의의 C HTTP 규약을 추정하지
않는다.

```python
from gateway.server import create_server

app = create_server(
    settings,
    send_events=approved_c_adapter,
    replay_options={"min_delay_s": 1, "max_delay_s": 60},
)
```

`approved_c_adapter(events)`는 합의된 중앙 저장·인증 계약을 구현하고 영속 확인
후 `BatchReceipt`를 반환해야 한다. 일반 dictionary receipt는 승인으로 인정하지
않는다. 이 어댑터의 HTTP 클라이언트와 연결 종료·잔여 송신 정리는 **호출자가
소유**한다. 서버는 어댑터에 사건 메타데이터만 전달하고 자체 detector/upstream
클라이언트 또는 journal 핸들을 전달하지 않는다.

lifespan에서 journal과 서버 소유 HTTP 클라이언트를 연 뒤 현재 게이트웨이 라우트를
설치하고 worker를 시작한다. 종료·시작 실패·취소 시 worker를 먼저 정지한 후
라우트, HTTP 클라이언트, journal을 정리한다. 다시 시작하면 새 journal을 연결하되
동일 shipper를 유지하므로 이전 송신이 취소를 무시해도 `SEND_DRAINING` 제한을
이어간다. 이전의 닫힌 journal을 가진 라우트는 남기지 않는다. 같은 서버의
동시에 겹친 lifespan은 거부한다.

중앙 장애 자체는 게이트웨이 readiness를 낮추지 않으며 사건은 로컬에 남는다.
기존 journal 저장 실패 시 요청 거부 정책은 그대로 유지한다.
`app.state.replay_worker`는 비활성이면 `None`, 활성 구성에서는 워커의
`last_report`/`error_code`를 관찰하는 용도다. 서버가 수명주기를 소유하므로 외부에서
해당 워커를 직접 start/stop하지 않는다. 실제 Gateway 요청으로 생성한 사건의
부분 승인·장애·재시작은 `test_replay_lifecycle.py`의 합성 어댑터로 검증한다.
