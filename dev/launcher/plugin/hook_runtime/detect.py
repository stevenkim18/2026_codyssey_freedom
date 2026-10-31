"""Local pattern rules plus the optional authenticated inspection API.

Uses the standard library only so that macOS system Python can run it. Without
detector settings only the representative patterns below apply: names, addresses
and accounts are found only in common formats or beside a keyword. A configured
detector that fails stops the caller (fail closed).
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
_CONTEXT_WINDOW = 30
_RRN = re.compile(_EDGE + r"(\d{2})(\d{2})(\d{2})(-\s*| )?[1-8]\d{6}" + _END)
_RRN_CONTEXT = re.compile(r"(?i)주민|등록번호|민번|\brrn\b|\bssn\b")
_CARD = re.compile(_EDGE + r"[3-6]\d(?:[ .-]?\d){11,17}" + _END)
_CARD_CONTEXT = re.compile(r"(?i)카드|결제|card")
_PHONE = re.compile(r"(?<![0-9A-Za-z.-])(?:\+82[- .]?1[016789]|\(?01[016789]\)?)[- .]?\d{3,4}[- .]?\d{4}" + _END
                    + r"|(?<![0-9A-Za-z.-])\(?0(?:2|[3-6]\d)\)?-?\d{3,4}-\d{4}" + _END)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_API_KEYS = re.compile(r"(?<![A-Za-z0-9_-])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}"
                       r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})"
                       r"|(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_VALUE = r"[\"']?([^\s\"'`,;()\[\]${}<>]{4,128})"
_CREDENTIAL = re.compile(r"(?i)(?:password|passwd|pwd|passphrase|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
                         r"[\"']?\s*[:=]\s*" + _VALUE
                         + r"|(?:비밀번호|패스워드|암호)(?:\s*[:=]\s*|\s*[은는이가]?\s+)" + _VALUE)
_PLACEHOLDERS = {"none", "null", "true", "false", "undefined", "string", "password", "changeme", "example", "********"}
_CONTEXT_RULES = (
    ("KR_BANK_ACCOUNT", re.compile(r"(?i)(?:계좌|입금|송금|이체|은행|account)[^\n]{0,20}?(?<![\d-])"
                                   r"(\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,3})?|\d{10,14})(?![\d-])")),
    ("KR_PASSPORT", re.compile(r"(?i)(?:여권|passport)[^\n]{0,15}?(?<![A-Za-z0-9])([A-Z]{1,2}\d{7,8})" + _END)),
    ("KR_NHIS", re.compile(r"(?:건강보험|건보)[^\n]{0,15}?(?<!\d)(\d{10,11})(?!\d)")),
    ("DATE_OF_BIRTH", re.compile(r"(?i)(?:생년월일|생일|출생일|birth)[^\n]{0,8}?"
                                 r"(\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}일?)")),
)
_PATTERN_RULES = (
    ("KR_DRIVER_LICENSE", re.compile(r"(?<![\d-])\d{2}-\d{2}-\d{6}-\d{2}(?![\d-])")),
    ("KR_VEHICLE_PLATE", re.compile(r"(?<![0-9가-힣])\d{2,3}[가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주아바사자배]"
                                    r"\s?\d{4}(?!\d)")),
    ("KR_LOCATION", re.compile(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[가-힣]*\s+"
                               r"[가-힣]+[시군구](?:\s+[가-힣]+[시군구])?\s+[가-힣0-9]+(?:로|길|동|읍|면)(?:\s*\d+(?:-\d+)?)?")),
)
_SURNAMES = "김이박최정강조윤장임한오서권황안송류홍"
_NAME_LABEL = re.compile(r"(?:이름|성명|수신인|보호자|예금주|환자명|고객명)(?:\s*[:은는]\s*|\s+)([" + _SURNAMES + r"][가-힣]{2})(?![가-힣])")
_NAME_TITLE = re.compile(r"(?<![가-힣])([" + _SURNAMES + r"][가-힣]{2})(?=\s?(?:님|씨|고객|과장|대리|부장|차장|팀장|사원|대표|교수"
                         r"|선생|환자|에게|한테|께서|께))")
_NOT_NAMES = {"정답은", "정답이", "이사장", "이사회", "이사람", "이친구", "이고객", "이학생", "정상인", "장기간", "조사관", "조사원", "한국인",
              "한사람", "임직원", "임대인", "임차인", "안내원", "안전팀", "오늘의", "서비스", "최고의", "강사진",
              "장애인", "황금의", "권한자", "윤리팀", "홍보팀", "송금인", "류학생", "박람회", "김치찌"}
_NOT_NAME_ENDINGS = "만를을의에로"
_SPACED_ENDINGS = "적간안주한는은도고면서"
_NORMALIZE = {0x3000: " ", 0xFF0E: ".", 0xFF20: "@", 0xFF0D: "-",
              **{code: "-" for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)},
              **{0xFF10 + offset: str(offset) for offset in range(10)},
              **{0xFF21 + offset: chr(65 + offset) for offset in range(26)},
              **{0xFF41 + offset: chr(97 + offset) for offset in range(26)}}
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


def _has_context(text, start, pattern):
    return pattern.search(text, max(0, start - _CONTEXT_WINDOW), start) is not None


def _is_credential(value):
    if not value.isascii() or value.lower() in _PLACEHOLDERS or re.fullmatch(r"\*+|x+|X+", value):
        return False
    # Identifiers such as self.password or YOUR_API_KEY are code, not credentials.
    return any(char.isdigit() or char not in "._-" and not char.isalpha() for char in value)


def _is_name(value, *, spaced=False):
    # Words such as 조만간 or 오전에 precede a job title but are not names.
    return value not in _NOT_NAMES and value[-1] not in (_NOT_NAME_ENDINGS + _SPACED_ENDINGS if spaced else _NOT_NAME_ENDINGS)


def local_findings(text):
    """Patterns run on a same-length folded copy, so spans stay valid for the original."""
    folded = text.translate(_NORMALIZE)
    # A colon after the host is an scp-style Git remote such as git@host:org/repo.
    found = [_finding("EMAIL_ADDRESS", m.start(), m.end()) for m in _EMAIL.finditer(folded)
             if not folded.startswith(":", m.end())]
    found += [_finding("KR_PHONE_NUMBER", m.start(), m.end()) for m in _PHONE.finditer(folded)]
    for match in _RRN.finditer(folded):
        # Thirteen bare digits are often barcodes or millisecond timestamps.
        if (1 <= int(match.group(2)) <= 12 and 1 <= int(match.group(3)) <= 31
                and (match.group(4) or _has_context(folded, match.start(), _RRN_CONTEXT))):
            found.append(_finding("KR_RRN", match.start(), match.end()))
    for match in _CARD.finditer(folded):
        digits = re.sub(r"\D", "", match.group())
        if (13 <= len(digits) <= 19 and _luhn(digits)
                and (len(digits) != len(match.group()) or _has_context(folded, match.start(), _CARD_CONTEXT))):
            found.append(_finding("CREDIT_CARD", match.start(), match.end()))
    found += [_finding("API_KEY", m.start(), m.end()) for m in _API_KEYS.finditer(folded)]
    found += [_finding("PRIVATE_KEY", m.start(), m.end()) for m in _PRIVATE_KEY.finditer(folded)]
    for match in _CREDENTIAL.finditer(folded):
        group = 1 if match.group(1) else 2
        if _is_credential(match.group(group)):
            found.append(_finding("API_KEY", match.start(group), match.end(group)))
    for kind, pattern in _CONTEXT_RULES:
        found += [_finding(kind, m.start(1), m.end(1)) for m in pattern.finditer(folded)]
    for kind, pattern in _PATTERN_RULES:
        found += [_finding(kind, m.start(), m.end()) for m in pattern.finditer(folded)]
    found += [_finding("PERSON", m.start(1), m.end(1)) for m in _NAME_LABEL.finditer(folded) if _is_name(m.group(1))]
    found += [_finding("PERSON", m.start(1), m.end(1)) for m in _NAME_TITLE.finditer(folded)
              if _is_name(m.group(1), spaced=folded[m.end(1)].isspace())]
    found += secret_rules.detect([{"segment_id": "s0", "text": folded}])
    return list({(item["type"], item["start"], item["end"]): item for item in found}.values())


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
