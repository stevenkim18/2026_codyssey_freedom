"""Fail-closed policy decisions over complete, codepoint-indexed inspections.

The caller must inspect a ``mask`` result once more before forwarding it. This
module never performs network I/O and never logs submitted content.
"""

from dataclasses import dataclass, field
import math


BLOCK_TYPES = frozenset({"KR_RRN", "CREDIT_CARD", "API_KEY", "PRIVATE_KEY"})
MASK_TYPES = frozenset({
    "PERSON", "EMAIL_ADDRESS", "KR_PHONE_NUMBER", "KR_BANK_ACCOUNT", "KR_LOCATION",
    "DATE_OF_BIRTH", "KR_DRIVER_LICENSE", "KR_PASSPORT", "KR_NHIS",
    "KR_VEHICLE_PLATE", "USERNAME", "PERSONAL_ATTRIBUTE",
})
ALLOW_TYPES = frozenset({"ORGANIZATION", "URL", "IP_ADDRESS", "NRP"})
PLACEHOLDER = "[REDACTED]"


@dataclass(frozen=True)
class Decision:
    action: str
    segments: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)


def _finite_number(value, minimum, maximum=math.inf):
    return (type(value) in (int, float) and math.isfinite(value)
            and minimum <= value <= maximum)


def _validated_segments(segments):
    if not isinstance(segments, list):
        raise ValueError
    result = {}
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError
        segment_id, text = segment.get("segment_id"), segment.get("text")
        if not isinstance(segment_id, str) or not segment_id or not isinstance(text, str):
            raise ValueError
        if segment_id in result:
            raise ValueError
        result[segment_id] = text
    return result


def _validated_findings(inspection, texts):
    if not isinstance(inspection, dict) or inspection.get("status") != "complete":
        raise ValueError
    if not isinstance(inspection.get("findings"), list):
        raise ValueError
    for key in ("model_version", "threshold_version"):
        if not isinstance(inspection.get(key), str) or not inspection[key].strip():
            raise ValueError
    if not _finite_number(inspection.get("timing_ms"), 0):
        raise ValueError
    findings = []
    for finding in inspection["findings"]:
        if not isinstance(finding, dict):
            raise ValueError
        segment_id, kind = finding.get("segment_id"), finding.get("type")
        start, end = finding.get("start"), finding.get("end")
        detector, score = finding.get("detector"), finding.get("score")
        if not isinstance(segment_id, str) or segment_id not in texts:
            raise ValueError
        if not isinstance(kind, str) or kind not in BLOCK_TYPES | MASK_TYPES | ALLOW_TYPES:
            raise ValueError
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(texts[segment_id]):
            raise ValueError
        if not _finite_number(score, 0, 1) or detector not in ("model", "rule"):
            raise ValueError
        if kind in {"API_KEY", "PRIVATE_KEY"} and detector != "rule":
            raise ValueError
        # Copy only contract fields: accidental raw-text extras never propagate.
        findings.append({"segment_id": segment_id, "type": kind, "start": start,
                         "end": end, "score": score, "detector": detector})
    return findings


def _mask(text, spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + PLACEHOLDER + text[end:]
    return text


def evaluate(segments, inspection):
    """Return allow/mask/block/error; block and error carry no text payload.

    Invalid findings take error precedence even if another finding would block.
    Python strings make offsets Unicode codepoints, including non-BMP emoji.
    Adjacent spans are merged as well as overlaps to avoid repeated placeholders.
    """
    try:
        texts = _validated_segments(segments)
        findings = _validated_findings(inspection, texts)
    except (ValueError, TypeError, OverflowError):
        return Decision("error", rule_ids=["policy.invalid_inspection"])
    rule_ids = sorted({f"policy.{'block' if item['type'] in BLOCK_TYPES else 'mask' if item['type'] in MASK_TYPES else 'warn'}.{item['type']}"
                       for item in findings})
    if any(item["type"] in BLOCK_TYPES for item in findings):
        return Decision("block", findings=findings, rule_ids=rule_ids)
    spans = {}
    for item in findings:
        if item["type"] in MASK_TYPES:
            spans.setdefault(item["segment_id"], []).append((item["start"], item["end"]))
    output = [{"segment_id": segment_id, "text": _mask(text, spans.get(segment_id, []))}
              for segment_id, text in texts.items()]
    return Decision("mask" if spans else "allow", output, findings, rule_ids)
