"""Plugin hook behavior through the actual /bin/sh wrapper and system Python.

All values are synthetic. Local loopback TLS stands in for the detector; no
model, provider, Codex home or server is contacted here.
"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
import threading

import pytest

from deployment.tests.test_gpu_tls import certificates  # noqa: F401
from gateway.journal import _validate_event

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "launcher/plugin"
RUN = PLUGIN / "hook_runtime/run.sh"
RRN = "900101-1234567"
CARD = "4111 1111 1111 1111"
EMAIL = "user@example.com"
PHONE = "010-1234-5678"


def execute(event, tmp_path, *, mode="guard", settings=None, run=RUN, data=None):
    payload = event if isinstance(event, (bytes, str)) else json.dumps(event, ensure_ascii=False)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"),
           "PLUGIN_DATA": str(data or tmp_path / "data"),
           "PII_HOOK_SETTINGS": str(settings or tmp_path / "no-settings.json")}
    return subprocess.run(["/bin/sh", str(run), mode], input=payload if isinstance(payload, bytes) else payload.encode(),
                          env=env, capture_output=True, timeout=30)


def prompt(text):
    return {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "turn_id": "turn-1", "prompt": text}


def tool(response):
    return {"hook_event_name": "PostToolUse", "session_id": "session-1", "turn_id": "turn-2",
            "tool_name": "Bash", "tool_response": response}


def stop(text, active=False):
    return {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": "turn-3",
            "stop_hook_active": active, "last_assistant_message": text}


def events(tmp_path):
    connection = sqlite3.connect(tmp_path / "data/events.sqlite3")
    try:
        rows = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events ORDER BY rowid")]
    finally:
        connection.close()
    for row in rows:
        _validate_event(row)
    return rows


def output(result):
    return json.loads(result.stdout)


def no_raw(result, tmp_path, *values):
    journal = (tmp_path / "data/events.sqlite3").read_bytes()
    for value in values:
        assert value.encode() not in result.stdout + result.stderr
        assert value.encode() not in journal


@pytest.mark.parametrize("name", ["policy.py", "secret_rules.py", "journal.py"])
def test_vendored_policy_is_the_gateway_source(name):
    original = (ROOT / "gateway" / name).read_bytes()
    assert hashlib.sha256((PLUGIN / "hook_runtime/vendor/gateway" / name).read_bytes()).digest() == hashlib.sha256(original).digest()


def test_hook_manifest_runs_guard_and_observer_within_codex_timeout():
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"}
    for event, mode in (("UserPromptSubmit", "guard"), ("PostToolUse", "guard"), ("Stop", "observe"), ("SessionStart", "observe")):
        handler = hooks[event][0]["hooks"][0]
        assert handler["type"] == "command" and handler["timeout"] == 30
        assert handler["command"] == f'/bin/sh "${{PLUGIN_ROOT}}/hook_runtime/run.sh" {mode}'


def test_clean_prompt_passes_and_is_recorded(tmp_path):
    result = execute(prompt("테스트 계획을 설명해 줘"), tmp_path)
    assert (result.returncode, result.stdout) == (0, b"")
    row = events(tmp_path)[0]
    assert (row["direction"], row["decision"], row["delivery_status"]) == ("outbound", "allow", "unknown")
    assert row["request_id"] == "turn-1" and row["policy_version"] == "plugin-hook-v1"


@pytest.mark.parametrize("value,kind", [(RRN, "KR_RRN"), (CARD, "CREDIT_CARD"),
                                        ("sk-proj-" + "a" * 48, "API_KEY")])
def test_blocking_prompt_stops_without_echoing_the_value(tmp_path, value, kind):
    result = execute(prompt(f"이 값 확인 {value} 부탁해"), tmp_path)
    decision = output(result)
    assert result.returncode == 0 and decision["decision"] == "block"
    no_raw(result, tmp_path, value)
    row = events(tmp_path)[0]
    assert (row["decision"], row["delivery_status"]) == ("block", "not_sent")
    assert row["findings"] == [{"type": kind, "count": 1}]


def test_maskable_prompt_stops_and_offers_only_masked_text(tmp_path):
    result = execute(prompt(f"{EMAIL} 와 {PHONE} 로 연락해 줘"), tmp_path)
    reason = output(result)["reason"]
    assert "[REDACTED] 와 [REDACTED] 로 연락해 줘" in reason
    no_raw(result, tmp_path, EMAIL, PHONE)
    row = events(tmp_path)[0]
    assert (row["decision"], row["delivery_status"]) == ("mask", "not_sent")


@pytest.mark.parametrize("text", [
    "카드 4111 1111 1111 1112 는 검증 숫자가 다릅니다",
    "991399-1234567 은 날짜가 아닙니다",
    "주문 ORD-2026-123456, 일련번호 SN1234567890123, 금액 12,000원",
    "버전 1.2.3 포트 8765 커밋 abcdef123456",
])
def test_lookalikes_are_not_flagged(tmp_path, text):
    result = execute(prompt(text), tmp_path)
    assert (result.returncode, result.stdout) == (0, b"")


def test_tool_output_with_maskable_value_is_replaced_by_masked_text(tmp_path):
    result = execute(tool(f"담당자 {EMAIL}\n상태 정상"), tmp_path)
    decision = output(result)
    assert decision["decision"] == "block" and decision["reason"] == "담당자 [REDACTED]\n상태 정상"
    no_raw(result, tmp_path, EMAIL)
    assert events(tmp_path)[0]["delivery_status"] == "forwarded_sanitized"


def test_structured_tool_output_is_inspected(tmp_path):
    result = execute(tool({"stdout": f"메일 {EMAIL}", "exit_code": 0}), tmp_path)
    assert EMAIL not in output(result)["reason"]


def test_tool_output_with_blocking_value_is_replaced_by_fixed_notice(tmp_path):
    result = execute(tool(f"고객 {RRN} 와 {EMAIL}"), tmp_path)
    assert "민감정보" in output(result)["reason"]
    no_raw(result, tmp_path, RRN, EMAIL)
    assert (events(tmp_path)[0]["decision"], events(tmp_path)[0]["delivery_status"]) == ("block", "not_sent")


def test_clean_tool_output_is_untouched(tmp_path):
    result = execute(tool("빌드 성공"), tmp_path)
    assert (result.returncode, result.stdout) == (0, b"")


def test_response_finding_requests_one_masked_answer_and_records_late_detection(tmp_path):
    result = execute(stop(f"고객 번호는 {RRN} 입니다"), tmp_path, mode="observe")
    assert output(result)["decision"] == "block"
    no_raw(result, tmp_path, RRN)
    row = events(tmp_path)[0]
    assert (row["direction"], row["delivery_status"], row["error_code"]) == ("inbound", "delivered", "POST_DISPLAY_FINDING")


def test_response_recheck_never_loops(tmp_path):
    result = execute(stop(f"고객 번호는 {RRN} 입니다", active=True), tmp_path, mode="observe")
    assert (result.returncode, result.stdout) == (0, b"")
    assert events(tmp_path)[0]["error_code"] == "POST_DISPLAY_FINDING"


def test_clean_response_has_no_late_finding(tmp_path):
    result = execute(stop("안녕하세요"), tmp_path, mode="observe")
    assert (result.returncode, result.stdout) == (0, b"")
    assert events(tmp_path)[0]["error_code"] is None


def test_session_start_reports_rule_only_mode(tmp_path):
    result = execute({"hook_event_name": "SessionStart", "session_id": "session-1"}, tmp_path, mode="observe")
    assert result.returncode == 0 and "규칙" in output(result)["systemMessage"]


@pytest.mark.parametrize("payload", [b"{not json", b"[]", b"\xff\xfe"])
def test_guard_blocks_unreadable_input(tmp_path, payload):
    assert execute(payload, tmp_path).returncode == 2


def test_guard_blocks_unknown_event(tmp_path):
    assert execute({"hook_event_name": "Unknown"}, tmp_path).returncode == 2


def test_wrapper_converts_interpreter_failure_into_guard_block(tmp_path):
    broken = tmp_path / "hook_runtime"
    broken.mkdir()
    shutil.copy(RUN, broken / "run.sh")  # main.py is deliberately absent.
    run = broken / "run.sh"
    assert execute(prompt("안녕"), tmp_path, run=run).returncode == 2
    assert execute(prompt("안녕"), tmp_path, run=run, mode="observe").returncode == 0


def test_journal_failure_blocks_guard_but_not_observer(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("not a directory")
    assert execute(prompt("안녕"), tmp_path, data=occupied).returncode == 2
    assert execute(stop("안녕"), tmp_path, mode="observe", data=occupied).returncode == 0


def test_watchdog_exits_with_guard_block_before_codex_timeout(tmp_path):
    code = ("import sys, time; sys.path.insert(0, %r); import main; "
            "main.start_watchdog('guard', 0.1); time.sleep(5)" % str(PLUGIN / "hook_runtime"))
    result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", "-c", code], capture_output=True, timeout=4)
    assert result.returncode == 2 and result.stderr


def test_doctor_reports_each_check_separately(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex"),
           "PLUGIN_DATA": str(tmp_path / "data"), "PII_HOOK_SETTINGS": str(tmp_path / "none.json")}
    (tmp_path / "codex").mkdir()
    (tmp_path / "codex/config.toml").write_text('[hooks.state."pii-gateway@codyssey-security:hooks/hooks.json:stop:0:0"]\n')
    result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", str(PLUGIN / "hook_runtime/main.py"), "--doctor"],
                            env=env, capture_output=True, timeout=20)
    report = json.loads(result.stdout)
    assert report["detection_mode"] == "rules_only" and report["self_test"] is True
    assert report["journal_writable"] is True and report["trust_recorded"] is True


class Detector:
    def __init__(self, certificate, key, reply):
        self.received = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.received.append((self.headers["authorization"], body))
                data = json.dumps(reply(body)).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def complete(findings=()):
    def reply(body):
        text = body["segments"][0]["text"]
        return {"schema_version": "1.0", "request_id": body["request_id"], "inspection_id": body["inspection_id"],
                "status": "complete", "model_version": "M0-test", "threshold_version": "t1", "timing_ms": 1.0,
                "timing_breakdown_ms": {}, "findings": [
                    {"segment_id": "s0", "type": kind, "start": text.index(word), "end": text.index(word) + len(word),
                     "score": 0.9, "detector": "model"} for kind, word in findings if word in text]}
    return reply


@pytest.fixture
def remote(tmp_path, certificates):  # noqa: F811
    certificate, key = certificates[0]

    def build(reply, *, port=None, mode=0o600):
        detector = Detector(certificate, key, reply) if port is None else None
        target = port or detector.port
        settings = tmp_path / "client.json"
        settings.write_text(json.dumps({"detector_url": f"https://127.0.0.1:{target}/v1/inspect",
                                        "detector_token": "synthetic-token", "detector_ca_file": str(certificate)}))
        settings.chmod(mode)
        return detector, settings
    return build


def test_detector_finding_joins_rules_and_masks_on_recheck(tmp_path, remote):
    detector, settings = remote(complete([("PERSON", "김가상")]))
    result = execute(prompt(f"김가상 에게 {EMAIL} 로 알려줘"), tmp_path, settings=settings)
    assert "[REDACTED] 에게 [REDACTED] 로 알려줘" in output(result)["reason"]
    no_raw(result, tmp_path, "김가상", EMAIL)
    assert len(detector.received) == 2  # Original text, then exactly one recheck.
    authorization, body = detector.received[0]
    assert authorization == "Bearer synthetic-token" and body["direction"] == "outbound"
    assert set(body) == {"schema_version", "request_id", "inspection_id", "direction", "segments"}
    row = events(tmp_path)[0]
    assert row["model_version"] == "M0-test"
    assert b"synthetic-token" not in result.stdout + result.stderr


def test_recheck_residue_stops_with_fixed_failure(tmp_path, remote):
    def reply(body):
        answer = complete([("PERSON", "김가상")])(body)
        if "[REDACTED]" in body["segments"][0]["text"]:
            answer["findings"] = [{"segment_id": "s0", "type": "PERSON", "start": 0, "end": 1, "score": 0.9, "detector": "model"}]
        return answer
    _, settings = remote(reply)
    result = execute(prompt("김가상 안내문"), tmp_path, settings=settings)
    assert "REDACTED" not in output(result)["reason"]
    assert events(tmp_path)[-1]["error_code"] == "REINSPECTION_FAILED"


@pytest.mark.parametrize("mutate", [
    lambda answer: answer.update(status="incomplete"),
    lambda answer: answer.update(inspection_id="other"),
    lambda answer: answer.update(findings=[{"segment_id": "s0", "type": "UNKNOWN", "start": 0, "end": 1, "score": 1, "detector": "model"}]),
    lambda answer: answer.update(findings=[{"segment_id": "s0", "type": "PERSON", "start": 0, "end": 999, "score": 1, "detector": "model"}]),
])
def test_invalid_detector_answer_blocks_instead_of_passing(tmp_path, remote, mutate):
    def reply(body):
        answer = complete()(body)
        mutate(answer)
        return answer
    _, settings = remote(reply)
    result = execute(prompt("평범한 질문"), tmp_path, settings=settings)
    assert output(result)["decision"] == "block"
    assert events(tmp_path)[-1]["decision"] == "error"


def test_configured_detector_outage_blocks(tmp_path, remote):
    _, settings = remote(None, port=9)
    result = execute(prompt("평범한 질문"), tmp_path, settings=settings)
    assert output(result)["decision"] == "block"
    assert events(tmp_path)[-1]["error_code"] == "INSPECTION_FAILED"


def test_settings_readable_by_others_are_refused(tmp_path, remote):
    detector, settings = remote(complete(), mode=0o644)
    result = execute(prompt("평범한 질문"), tmp_path, settings=settings)
    assert output(result)["decision"] == "block" and detector.received == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/v1/inspect", "https://127.0.0.1:9/other"])
def test_only_https_inspect_destinations_are_accepted(tmp_path, url):
    settings = tmp_path / "client.json"
    settings.write_text(json.dumps({"detector_url": url, "detector_token": "synthetic-token"}))
    settings.chmod(0o600)
    assert output(execute(prompt("평범한 질문"), tmp_path, settings=settings))["decision"] == "block"


def test_overlong_text_is_blocked_for_detector_never_truncated(tmp_path, remote):
    detector, settings = remote(complete())
    result = execute(prompt("가" * 16001), tmp_path, settings=settings)
    assert output(result)["decision"] == "block" and detector.received == []


def test_detector_outage_does_not_loop_on_stop(tmp_path, remote):
    _, settings = remote(None, port=9)
    result = execute(stop("안녕하세요"), tmp_path, mode="observe", settings=settings)
    assert (result.returncode, result.stdout) == (0, b"")


DEMO_RULES = [
    # False positives that must stay usable.
    ("상품 8801234567890 재고 확인", "allow", None), ("ts=1701012345678 처리", "allow", None),
    ("주문 2101154567890 조회", "allow", None), ("id 4532015112830366 조회", "allow", None),
    ("git@github.com:org/repo.git 를 클론해 줘", "allow", None), ("npm i @types/node", "allow", None),
    ("password=os.environ[\"DB\"]", "allow", None), ("password: ${DB_PASS}", "allow", None),
    ("api_key=YOUR_API_KEY", "allow", None), ("password=self.password", "allow", None),
    ("비밀번호를 입력해 주세요", "allow", None), ("암호화된 값을 저장해", "allow", None),
    ("담당자에게 전달해 줘", "allow", None), ("전문가에게 물어봐", "allow", None), ("임직원에게 공지해", "allow", None),
    ("이사장님께 보고해", "allow", None), ("신청자에게 안내해", "allow", None), ("고객님께 사과해", "allow", None),
    ("원격 비밀번호 인증·호스트 확인", "allow", None), ("출력 파일은 새 이름이어야 한다", "allow", None),
    ("이름 정답만 빼고 처리", "allow", None), ("이름 정답은 그대로 둬", "allow", None), ("조만간 과장님께 보고할게", "allow", None), ("한동안 대표님이 안 계셔", "allow", None),
    ("이번주 팀장님 일정", "allow", None), ("오전에 고객님께 전화", "allow", None), ("정기적 사원 교육", "allow", None),
    ("30분 1234개 처리", "allow", None), ("계좌 개설 방법 알려줘", "allow", None), ("서울 날씨 알려줘", "allow", None),
    # Format variants of supported values.
    ("주민번호 900101–1234567", "block", "KR_RRN"), ("900101-\n1234567", "block", "KR_RRN"),
    ("９００１０１-１２３４５６７", "block", "KR_RRN"), ("주민번호 9001011234567", "block", "KR_RRN"),
    ("카드 4111.1111.1111.1111", "block", "CREDIT_CARD"), ("카드번호 4111111111111111", "block", "CREDIT_CARD"),
    ("(010)1234-5678", "mask", "KR_PHONE_NUMBER"), ("user＠example.com", "mask", "EMAIL_ADDRESS"),
    # Secrets.
    ("ghp_" + "a" * 36, "block", "API_KEY"), ("AKIAIOSFODNN7EXAMPLE", "block", "API_KEY"),
    ("sk-abcdefghijklmnopqrstuvwxyz123456", "block", "API_KEY"), ("xoxb-1234567890-abcdefghij", "block", "API_KEY"),
    ("AIza" + "a" * 35, "block", "API_KEY"), ("-----BEGIN OPENSSH PRIVATE KEY-----", "block", "PRIVATE_KEY"),
    ("password=hunter2", "block", "API_KEY"), ("DB 비밀번호는 Qwer1234! 야", "block", "API_KEY"),
    ("비밀번호: 1234", "block", "API_KEY"),
    # Rule-detectable personal data.
    ("국민은행 123-456-789012 로 입금해", "mask", "KR_BANK_ACCOUNT"), ("계좌번호 110123456789", "mask", "KR_BANK_ACCOUNT"),
    ("여권 M12345678", "mask", "KR_PASSPORT"), ("운전면허 11-22-333333-44", "mask", "KR_DRIVER_LICENSE"),
    ("건강보험 번호 12345678901", "mask", "KR_NHIS"), ("차량 12가 3456 조회", "mask", "KR_VEHICLE_PLATE"),
    ("서울특별시 강남구 테헤란로 123 으로 보내", "mask", "KR_LOCATION"), ("생년월일 1990-01-01", "mask", "DATE_OF_BIRTH"),
    ("이름: 김가상", "mask", "PERSON"), ("김가상에게 메일 보내줘", "mask", "PERSON"), ("홍길동 과장님 일정", "mask", "PERSON"), ("수신인 이민수 확인", "mask", "PERSON"),
]


@pytest.fixture(scope="module")
def demo_verdicts():
    code = ("import json, sys; sys.path[:0] = [%r, %r]; import detect\n"
            "out = []\n"
            "for text in json.load(sys.stdin):\n"
            "    decision, _ = detect.inspect(text, 'outbound', 'test', None)\n"
            "    out.append([decision.action, sorted({item['type'] for item in decision.findings}), decision.segments[0]['text'] if decision.segments else ''])\n"
            "print(json.dumps(out))" % (str(PLUGIN / "hook_runtime"), str(PLUGIN / "hook_runtime/vendor")))
    result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", "-c", code], capture_output=True, timeout=30,
                            input=json.dumps([case[0] for case in DEMO_RULES]).encode())
    assert result.returncode == 0, result.stderr.decode()[-400:]
    return dict(zip(range(len(DEMO_RULES)), json.loads(result.stdout)))


@pytest.mark.parametrize("index,case", list(enumerate(DEMO_RULES)), ids=lambda value: value[0][:24] if isinstance(value, tuple) else "")
def test_demo_rule_outcomes(demo_verdicts, index, case):
    action, kinds, _ = demo_verdicts[index]
    assert action == case[1], kinds
    if case[2]:
        assert case[2] in kinds


def test_fullwidth_masking_keeps_original_offsets(tmp_path):
    result = execute(prompt("메일 user＠example.com 입니다"), tmp_path)
    assert "메일 [REDACTED] 입니다" in output(result)["reason"]


def test_system_python_is_the_supported_runtime():
    version = subprocess.run(["/usr/bin/python3", "-c", "import sys; print(sys.version_info[:2] >= (3, 9))"],
                             capture_output=True, text=True, timeout=10)
    assert version.stdout.strip() == "True" and sys.version_info >= (3, 9)
    assert os.access(RUN, os.R_OK)
