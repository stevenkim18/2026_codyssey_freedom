"""Conservative supplemental secret rules, independent of PII model labels.

Only project-scoped OpenAI-style keys, explicitly assigned AWS access IDs, and
structurally valid unencrypted DER private-key PEM blocks are recognized. These
are format checks, not provider authentication or cryptographic key validation.
Unsupported secret formats remain a documented detection limit.
"""

import base64
import binascii
import re


_OPENAI = re.compile(r"(?<![A-Za-z0-9_-])sk-proj-[A-Za-z0-9_-]{48,256}(?![A-Za-z0-9_-])")
_AWS = re.compile(r"\bAWS_ACCESS_KEY_ID\s*[:=]\s*[\"']?(?P<key>AKIA[A-Z0-9]{16})(?![A-Za-z0-9])")
_PEM = re.compile(
    r"-----BEGIN (?P<label>RSA PRIVATE KEY|EC PRIVATE KEY|PRIVATE KEY)-----\r?\n"
    r"(?P<body>[A-Za-z0-9+/=\r\n]+)\r?\n-----END (?P=label)-----"
)


def _read_der(data, offset):
    if offset + 2 > len(data):
        raise ValueError
    tag, length = data[offset], data[offset + 1]
    offset += 2
    if length & 128:
        count = length & 127
        if not 1 <= count <= 4 or offset + count > len(data) or data[offset] == 0:
            raise ValueError
        length = int.from_bytes(data[offset:offset + count], "big")
        if length < 128:
            raise ValueError
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError
    return tag, data[offset:end], end


def _private_key_shape(body, label):
    try:
        der = base64.b64decode("".join(body.split()), validate=True)
        if not 64 <= len(der) <= 32768:
            return False
        tag, sequence, end = _read_der(der, 0)
        if tag != 0x30 or end != len(der):
            return False
        children = []
        offset = 0
        while offset < len(sequence):
            tag, value, offset = _read_der(sequence, offset)
            children.append((tag, value))
        if not children or children[0] not in ((2, b"\x00"), (2, b"\x01")):
            return False
        if label == "RSA PRIVATE KEY":
            return len(children) >= 9 and all(tag == 2 and value for tag, value in children[:9])
        if label == "EC PRIVATE KEY":
            return len(children) >= 2 and children[1][0] == 4 and len(children[1][1]) >= 24
        return (len(children) >= 3 and children[1][0] == 0x30 and children[1][1]
                and children[2][0] == 4 and len(children[2][1]) >= 24)
    except (ValueError, binascii.Error):
        return False


def detect(segments):
    """Return codepoint spans; malformed input raises a content-free ValueError."""
    if not isinstance(segments, list):
        raise ValueError("invalid segments")
    texts = {}
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("invalid segments")
        segment_id, text = segment.get("segment_id"), segment.get("text")
        if not isinstance(segment_id, str) or not segment_id or not isinstance(text, str) or segment_id in texts:
            raise ValueError("invalid segments")
        texts[segment_id] = text
    findings = []
    for segment_id, text in texts.items():
        spans = [(match.start(), match.end(), "API_KEY") for match in _OPENAI.finditer(text)]
        spans.extend((match.start("key"), match.end("key"), "API_KEY") for match in _AWS.finditer(text))
        spans.extend((match.start(), match.end(), "PRIVATE_KEY") for match in _PEM.finditer(text)
                     if _private_key_shape(match.group("body"), match.group("label")))
        findings.extend({"segment_id": segment_id, "type": kind, "start": start,
                         "end": end, "score": 1.0, "detector": "rule"}
                        for start, end, kind in sorted(set(spans)))
    return findings
