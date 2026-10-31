"""Local pattern rules plus the optional authenticated inspection API.

Uses the standard library only so that macOS system Python can run it. Without
detector settings only the patterns below apply; names and other model-only types
are not detected. A configured detector that fails stops the caller (fail closed).
"""
import json
import os
from pathlib import Path
import re
import ssl
import stat
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

from gateway import policy, secret_rules


RULE_VERSION = "local-rules-v1"
SETTINGS_LIMIT = 64 * 1024
RESPONSE_LIMIT = 1024 * 1024
DETECTOR_TEXT_LIMIT = 16000
LOCAL_TEXT_LIMIT = 1000000
REQUEST_TIMEOUT_S = 10

_EDGE = r"(?<![0-9A-Za-z])"
_END = r"(?![0-9A-Za-z])"
_RRN = re.compile(_EDGE + r"(\d{2})(\d{2})(\d{2})[- ]?[1-8]\d{6}" + _END)
_CARD = re.compile(_EDGE + r"[3-6]\d(?:[ -]?\d){11,17}" + _END)
_PHONE = re.compile(r"(?<![0-9A-Za-z.-])(?:\+82[- .]?1[016789]|01[016789])[- .]?\d{3,4}[- .]?\d{4}" + _END
                    + r"|(?<![0-9A-Za-z.-])0(?:2|[3-6]\d)-\d{3,4}-\d{4}" + _END)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_FINDING_KEYS = {"segment_id", "type", "start", "end", "score", "detector"}


class InspectionError(Exception):
    """Fixed code only; remote bodies and submitted text never enter the message."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def _luhn(digits):
    total = 0
    for index, digit in enumerate(int(char) for char in reversed(digits)):
        doubled = digit * 2 if index % 2 else digit
        total += doubled - 9 if doubled > 9 else doubled
    return total % 10 == 0


def _finding(kind, start, end):
    return {"segment_id": "s0", "type": kind, "start": start, "end": end, "score": 1.0, "detector": "rule"}


def local_findings(text):
    found = [_finding("EMAIL_ADDRESS", m.start(), m.end()) for m in _EMAIL.finditer(text)]
    found += [_finding("KR_PHONE_NUMBER", m.start(), m.end()) for m in _PHONE.finditer(text)]
    found += [_finding("KR_RRN", m.start(), m.end()) for m in _RRN.finditer(text)
              if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31]
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(_finding("CREDIT_CARD", match.start(), match.end()))
    return found + secret_rules.detect([{"segment_id": "s0", "text": text}])


def settings_path():
    configured = os.environ.get("PII_HOOK_SETTINGS")
    return Path(configured) if configured else Path.home() / ".config/ai-pii-gateway/client.json"


def load_settings():
    """Return None when unset; unsafe or malformed settings stop the caller."""
    path = settings_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise InspectionError("INVALID_SETTINGS") from None
    try:
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077 or not 0 < info.st_size <= SETTINGS_LIMIT):
            raise ValueError
        data = json.loads(path.read_bytes().decode("utf-8"))
        url, token = data["detector_url"], data["detector_token"]
        ca_file = data.get("detector_ca_file")
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path != "/v1/inspect" or port == 0
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError
        if not isinstance(token, str) or not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError
        if ca_file is not None and (not isinstance(ca_file, str) or not Path(ca_file).is_absolute()
                                    or not Path(ca_file).is_file()):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, AttributeError):
        raise InspectionError("INVALID_SETTINGS") from None
    return {"url": url, "token": token, "ca_file": ca_file}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _post(settings, body):
    try:
        context = ssl.create_default_context(cafile=settings["ca_file"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                             urllib.request.HTTPSHandler(context=context))
        request = urllib.request.Request(settings["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         method="POST", headers={"Authorization": "Bearer " + settings["token"],
                                                                 "Content-Type": "application/json",
                                                                 "Accept": "application/json"})
        with opener.open(request, timeout=REQUEST_TIMEOUT_S) as response:
            if response.status != 200:
                raise ValueError
            content = response.read(RESPONSE_LIMIT + 1)
        if len(content) > RESPONSE_LIMIT:
            raise ValueError
        result = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (OSError, ValueError, ssl.SSLError, urllib.error.URLError, UnicodeError):
        raise InspectionError("INSPECTION_FAILED") from None


def _remote(text, direction, request_id, settings):
    inspection_id = str(uuid.uuid4())
    answer = _post(settings, {"schema_version": "1.0", "request_id": request_id, "inspection_id": inspection_id,
                              "direction": direction, "segments": [{"segment_id": "s0", "text": text}]})
    findings = answer.get("findings")
    if (answer.get("schema_version") != "1.0" or answer.get("request_id") != request_id
            or answer.get("inspection_id") != inspection_id or answer.get("status") != "complete"
            or not isinstance(findings, list)
            or any(not isinstance(item, dict) or set(item) != _FINDING_KEYS for item in findings)):
        raise InspectionError("INSPECTION_INVALID")
    return answer


def _evaluate(text, direction, request_id, settings):
    limit = DETECTOR_TEXT_LIMIT if settings else LOCAL_TEXT_LIMIT
    if len(text) > limit:
        raise InspectionError("TEXT_LIMIT")
    findings, model_version, threshold_version, timing = local_findings(text), RULE_VERSION, RULE_VERSION, 0.0
    if settings:
        answer = _remote(text, direction, request_id, settings)
        findings = findings + answer["findings"]
        model_version = answer.get("model_version")
        threshold_version = answer.get("threshold_version")
        timing = answer.get("timing_ms")
    segments = [{"segment_id": "s0", "text": text}]
    decision = policy.evaluate(segments, {"status": "complete", "findings": findings, "model_version": model_version,
                                          "threshold_version": threshold_version, "timing_ms": timing})
    if decision.action == "error":
        raise InspectionError("INSPECTION_INVALID")
    return decision, model_version


def inspect(text, direction, request_id, settings):
    """Return (Decision, model_version); masked text was rechecked exactly once."""
    decision, version = _evaluate(text, direction, request_id, settings)
    if decision.action == "mask":
        try:
            recheck, recheck_version = _evaluate(decision.segments[0]["text"], direction, request_id, settings)
        except InspectionError:
            raise InspectionError("REINSPECTION_FAILED") from None
        if recheck.action != "allow" or recheck_version != version:
            raise InspectionError("REINSPECTION_FAILED")
    return decision, version
