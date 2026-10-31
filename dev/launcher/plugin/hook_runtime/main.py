"""Codex lifecycle hook entry point.

guard (prompt, tool result): every failure ends in a block, because Codex forwards
the content when a hook crashes or times out. observe (session, response): never
stops Codex. Responses are already on screen here, so this cannot prevent display.
Original values never enter stdout, stderr or events.
"""
import json
import os
from pathlib import Path
import sys
import threading
import time

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE / "vendor")]

import detect  # noqa: E402
import events  # noqa: E402


INPUT_LIMIT = 8 * 1024 * 1024
WATCHDOG_S = 25  # Below the 30-second hook timeout, after which Codex would forward.
TYPE_LABELS = {"KR_RRN": "주민등록번호", "CREDIT_CARD": "카드번호", "API_KEY": "API 키·비밀번호", "PRIVATE_KEY": "개인 키",
               "PERSON": "이름", "EMAIL_ADDRESS": "이메일", "KR_PHONE_NUMBER": "전화번호",
               "KR_BANK_ACCOUNT": "계좌번호", "KR_LOCATION": "주소", "DATE_OF_BIRTH": "생년월일",
               "KR_DRIVER_LICENSE": "운전면허번호", "KR_PASSPORT": "여권번호", "KR_NHIS": "건강보험번호",
               "KR_VEHICLE_PLATE": "차량번호", "USERNAME": "사용자 이름", "PERSONAL_ATTRIBUTE": "개인 속성"}
FAILURE_MESSAGES = {"INVALID_SETTINGS": "검사 설정이 안전하지 않아 전송을 멈췄습니다.",
                    "TEXT_LIMIT": "글이 검사 가능한 길이를 넘어 전송을 멈췄습니다."}
TOOL_BLOCKED = "[도구 결과 차단: 민감정보가 포함되어 LLM에 전달하지 않았습니다]"
TOOL_FAILED = "[도구 결과 차단: 검사를 완료하지 못해 LLM에 전달하지 않았습니다]"
RESPONSE_RETRY = ("직전 답변에 개인정보로 보이는 값이 있었습니다. 해당 값을 [REDACTED]로 가려 같은 내용을 다시 답하세요. "
                  "실제 값을 반복하지 마세요.")
GUARD_FAILED = "PII 검사를 완료하지 못해 전송을 멈췄습니다."


def write(stream, text):
    # -I -S under a C locale must not fail on Korean output.
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def emit(value):
    write(sys.stdout, json.dumps(value, ensure_ascii=False))


def start_watchdog(mode, seconds):
    def expire():
        if mode == "guard":
            write(sys.stderr, GUARD_FAILED + "\n")
            os._exit(2)
        os._exit(0)

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()


def _summary(findings):
    kinds = {}
    for item in findings:
        label = TYPE_LABELS.get(item["type"])
        if label:
            kinds[label] = kinds.get(label, 0) + 1
    return ", ".join(f"{label} {count}건" for label, count in kinds.items())


def _record_failure(data, direction, started, code):
    events.record(data, direction=direction, decision="error", delivery_status="not_sent", findings=[],
                  rule_ids=["hook.inspection_failed"], model_version="unknown", started=started, error_code=code)


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def on_prompt(data):
    started = time.monotonic()
    rule = "hook.user_prompt"
    try:
        settings = detect.load_settings()
        decision, version = detect.inspect(data["prompt"], "outbound", events._identifier(data.get("turn_id")), settings)
    except detect.InspectionError as failure:
        _record_failure(data, "outbound", started, failure.code)
        emit({"decision": "block", "reason": FAILURE_MESSAGES.get(failure.code, "검사 서버 응답을 확인하지 못해 전송을 멈췄습니다.")})
        return
    common = {"direction": "outbound", "decision": decision.action, "findings": decision.findings,
              "rule_ids": decision.rule_ids + [rule], "model_version": version, "started": started}
    if decision.action == "allow":
        events.record(data, delivery_status="unknown", **common)
        return
    events.record(data, delivery_status="not_sent", **common)
    if decision.action == "block":
        emit({"decision": "block", "reason": f"전송을 막았습니다: {_summary(decision.findings)}. 해당 값을 지우고 다시 입력해 주세요."})
    else:
        emit({"decision": "block", "reason": f"개인정보가 있어 전송을 멈췄습니다 ({_summary(decision.findings)}). "
                                             f"아래 문장으로 다시 보내 주세요.\n\n{decision.segments[0]['text']}"})


def on_tool_result(data):
    started = time.monotonic()
    rule = "hook.tool_output"
    try:
        settings = detect.load_settings()
        decision, version = detect.inspect(_text(data["tool_response"]), "outbound",
                                           events._identifier(data.get("turn_id")), settings)
    except detect.InspectionError as failure:
        _record_failure(data, "outbound", started, failure.code)
        emit({"decision": "block", "reason": TOOL_FAILED})
        return
    common = {"direction": "outbound", "decision": decision.action, "findings": decision.findings,
              "rule_ids": decision.rule_ids + [rule], "model_version": version, "started": started}
    if decision.action == "allow":
        events.record(data, delivery_status="unknown", **common)
    elif decision.action == "block":
        events.record(data, delivery_status="not_sent", **common)
        emit({"decision": "block", "reason": TOOL_BLOCKED})
    else:
        events.record(data, delivery_status="forwarded_sanitized", **common)
        emit({"decision": "block", "reason": decision.segments[0]["text"]})


def on_stop(data):
    started = time.monotonic()
    try:
        settings = detect.load_settings()
        decision, version = detect.inspect(data.get("last_assistant_message") or "", "inbound",
                                           events._identifier(data.get("turn_id")), settings)
    except detect.InspectionError as failure:
        _record_failure(data, "inbound", started, failure.code)
        return
    late = decision.action in ("mask", "block")
    events.record(data, direction="inbound", decision=decision.action, delivery_status="delivered",
                  findings=decision.findings, rule_ids=decision.rule_ids + ["hook.response"], model_version=version,
                  started=started, error_code="POST_DISPLAY_FINDING" if late else None)
    if late and not data.get("stop_hook_active"):
        emit({"decision": "block", "reason": RESPONSE_RETRY})


def on_session(_data):
    try:
        mode = "모델 + 규칙 검사" if detect.load_settings() else "규칙 검사 (대표 형식만 탐지)"
        emit({"systemMessage": f"PII 보호 켜짐: {mode}"})
    except detect.InspectionError:
        emit({"systemMessage": "PII 검사 설정 오류: 입력 전송이 차단됩니다."})


HANDLERS = {"UserPromptSubmit": ("guard", on_prompt), "PostToolUse": ("guard", on_tool_result),
            "Stop": ("observe", on_stop), "SessionStart": ("observe", on_session)}


def dispatch(mode):
    raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
    if len(raw) > INPUT_LIMIT:
        raise ValueError
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError
    expected, handler = HANDLERS[data["hook_event_name"]]
    if expected != mode:
        raise ValueError
    handler(data)


def doctor():
    report = {"python": ".".join(map(str, sys.version_info[:3])), "detection_mode": "rules_only",
              "self_test": False, "journal_writable": False, "trust_recorded": False}
    try:
        report["detection_mode"] = "detector" if detect.load_settings() else "rules_only"
    except detect.InspectionError:
        report["detection_mode"] = "invalid_settings"
    blocked, _ = detect.inspect("900101-1234567", "outbound", "doctor", None)
    clean, _ = detect.inspect("안녕하세요", "outbound", "doctor", None)
    report["self_test"] = blocked.action == "block" and clean.action == "allow"
    try:
        with events.open_journal():
            report["journal_writable"] = True
    except Exception:
        pass
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        report["trust_recorded"] = '[hooks.state."pii-gateway@' in (home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(report))
    return 0 if report["self_test"] and report["journal_writable"] else 1


def main(argv):
    if argv == ["--doctor"]:
        return doctor()
    mode = argv[0] if argv else "guard"
    start_watchdog(mode, WATCHDOG_S)
    try:
        dispatch(mode)
        return 0
    except Exception:
        if mode != "guard":
            return 0
        write(sys.stderr, GUARD_FAILED + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
