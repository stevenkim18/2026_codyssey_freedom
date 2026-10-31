"""R03/T03/T05: format-constrained secrets; all examples are synthetic."""

import base64

import pytest

from gateway.secret_rules import detect


SYNTHETIC_OPENAI = "sk-proj-" + "SyntheticFixture0123456789" * 3
SYNTHETIC_AWS = "AKIA" + "SYNTHETIC1234567"


def test_openai_fixed_format_reports_unicode_codepoints():
    text = "😀 token=" + SYNTHETIC_OPENAI
    findings = detect([{"segment_id": "s", "text": text}])
    assert len(findings) == 1
    finding = findings[0]
    assert finding == {"segment_id": "s", "type": "API_KEY", "start": 8,
                       "end": len(text), "score": 1.0, "detector": "rule"}


def test_aws_identifier_requires_explicit_credential_context():
    assert detect([{"segment_id": "s", "text": SYNTHETIC_AWS}]) == []
    text = "AWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS
    finding = detect([{"segment_id": "s", "text": text}])[0]
    assert text[finding["start"]:finding["end"]] == SYNTHETIC_AWS


@pytest.mark.parametrize("text", ["[REDACTED]", "sk-proj-example", "sk-example",
    "email: synthetic@example.invalid", "-----BEGIN PRIVATE KEY-----\nplaceholder\n-----END PRIVATE KEY-----"])
def test_placeholders_and_weak_patterns_are_not_secret_findings(text):
    assert detect([{"segment_id": "s", "text": text}]) == []


def test_structurally_valid_synthetic_private_key_block():
    # Synthetic DER PKCS#8 structure: version, algorithm identifier, private OCTET STRING.
    content = b"\x02\x01\x00\x30\x04\x06\x02\x2a\x03\x04\x40" + bytes(range(64))
    der = b"\x30" + bytes([len(content)]) + content
    pem = "-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----"
    text = "😀\n" + pem + "\nsuffix"
    finding = detect([{"segment_id": "s", "text": text}])[0]
    assert finding["type"] == "PRIVATE_KEY"
    assert text[finding["start"]:finding["end"]] == pem


def test_invalid_segments_rejected_without_echoing_input():
    with pytest.raises(ValueError, match="invalid segments"):
        detect([{"segment_id": "s", "text": SYNTHETIC_OPENAI}, {"segment_id": "s", "text": ""}])


def der(tag, payload):
    """Encode synthetic TLV fixtures; these are not usable cryptographic keys."""
    length = len(payload)
    if length < 128:
        encoded_length = bytes([length])
    else:
        byte_count = (length.bit_length() + 7) // 8
        encoded_length = bytes([0x80 | byte_count]) + length.to_bytes(byte_count, "big")
    return bytes([tag]) + encoded_length + payload


def pem(data, label="PRIVATE KEY", newline="\n"):
    return f"-----BEGIN {label}-----{newline}" + base64.b64encode(data).decode() + f"{newline}-----END {label}-----"


def private_key_der(label="PRIVATE KEY", key_length=64):
    version = der(2, b"\x01" if label == "EC PRIVATE KEY" else b"\x00")
    if label == "RSA PRIVATE KEY":
        content = version + b"".join(der(2, bytes([index]) * 16) for index in range(1, 9))
    elif label == "EC PRIVATE KEY":
        content = version + der(4, bytes(range(key_length)))
    else:
        content = version + der(0x30, der(6, b"\x2a\x03")) + der(4, bytes(range(key_length)))
    return der(0x30, content)


@pytest.mark.parametrize("label,key_length", [("PRIVATE KEY", 64), ("PRIVATE KEY", 200),
    ("RSA PRIVATE KEY", 64), ("EC PRIVATE KEY", 64)])
def test_supported_synthetic_der_shapes_and_crlf_preserve_complete_unicode_span(label, key_length):
    encoded = pem(private_key_der(label, key_length), label, newline="\r\n")
    text = "😀한글\n" + encoded + "\n끝"
    findings = detect([{"segment_id": "unicode", "text": text}])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["type"] == "PRIVATE_KEY"
    assert finding["segment_id"] == "unicode"
    assert (finding["start"], finding["end"]) == (4, 4 + len(encoded))
    assert text[finding["start"]:finding["end"]] == encoded


@pytest.mark.parametrize("data", [
    b"short synthetic data", b"x" * 32769,
    der(0x31, b"x" * 80), private_key_der() + b"trailing",
    der(0x30, der(2, b"\x02") + der(4, b"x" * 64)),
    # BER indefinite lengths, overflowing lengths, nonminimal DER lengths, truncation.
    b"\x30\x80" + b"x" * 80,
    b"\x30\x85" + b"x" * 80,
    b"\x30\x82\x00\x50" + b"x" * 80,
    b"\x30\x81\x3f" + b"x" * 63,
    b"\x30\x82\x01\x00" + b"x" * 80,
    der(0x30, der(2, b"\x00") + der(4, b"x" * 64) + b"\x04"),
    der(0x30, der(2, b"\x00") + der(4, b"x" * 64) + b"\x04\x82\x01"),
    # PKCS#8-like records with no algorithm, wrong private value tag, or tiny key.
    der(0x30, der(2, b"\x00") + der(4, b"x" * 64)),
    der(0x30, der(2, b"\x00") + der(0x30, b"") + der(4, b"x" * 64)),
    der(0x30, der(2, b"\x00") + der(0x30, der(6, b"\x2a\x03")) + der(2, b"x" * 64)),
    der(0x30, der(2, b"\x00") + der(0x30, der(6, b"x" * 64)) + der(4, b"x")),
])
def test_malformed_or_truncated_der_is_not_reported_as_validated_secret(data):
    assert detect([{"segment_id": "s", "text": pem(data)}]) == []


@pytest.mark.parametrize("label,content", [
    ("RSA PRIVATE KEY", der(2, b"\x00") + b"".join(der(2, b"x" * 16) for _ in range(7))),
    ("RSA PRIVATE KEY", der(2, b"\x00") + der(2, b"") + b"".join(der(2, b"x" * 16) for _ in range(7))),
    ("EC PRIVATE KEY", der(2, b"\x01") + der(2, b"x" * 64)),
    ("EC PRIVATE KEY", der(2, b"\x01") + der(4, b"x") + der(0xA1, b"x" * 64)),
])
def test_private_key_label_requires_its_expected_shape(label, content):
    assert detect([{"segment_id": "s", "text": pem(der(0x30, content), label)}]) == []


@pytest.mark.parametrize("text", [
    "-----BEGIN PRIVATE KEY-----\n" + "====" * 30 + "\n-----END PRIVATE KEY-----",
    pem(private_key_der()).replace("END PRIVATE KEY", "END RSA PRIVATE KEY"),
    pem(private_key_der()).replace("BEGIN PRIVATE KEY", "BEGIN PUBLIC KEY"),
    pem(private_key_der()).replace("-----END PRIVATE KEY-----", ""),
])
def test_invalid_base64_mismatched_labels_and_incomplete_pem_are_not_validated(text):
    assert detect([{"segment_id": "s", "text": text}]) == []


@pytest.mark.parametrize("body_length,expected", [(47, 0), (48, 1), (256, 1), (257, 0)])
def test_openai_length_bounds_never_emit_a_truncated_token(body_length, expected):
    key = "sk-proj-" + "S" * body_length
    text = "😀'" + key + "'"
    findings = detect([{"segment_id": "s", "text": text}])
    assert len(findings) == expected
    if findings:
        assert text[findings[0]["start"]:findings[0]["end"]] == key


@pytest.mark.parametrize("prefix,suffix,expected", [("x", "", 0), ("_", "", 0), ("-", "", 0),
    ("'", "'", 1), ("😀", "한글", 1)])
def test_openai_token_boundaries_and_unicode_context(prefix, suffix, expected):
    text = prefix + SYNTHETIC_OPENAI + suffix
    findings = detect([{"segment_id": "s", "text": text}])
    assert len(findings) == expected
    if findings:
        assert text[findings[0]["start"]:findings[0]["end"]] == SYNTHETIC_OPENAI


@pytest.mark.parametrize("text,expected", [
    ("😀AWS_ACCESS_KEY_ID : '" + SYNTHETIC_AWS + "'", 1),
    ("AWS_ACCESS_KEY_ID=\"" + SYNTHETIC_AWS + "\"", 1),
    ("NOT_AWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS, 0),
    ("aws_access_key_id=" + SYNTHETIC_AWS, 0),
    ("AWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS + "A", 0),
    ("AWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS[:-1], 0),
    ("AWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS.lower(), 0),
])
def test_aws_explicit_context_and_complete_identifier_boundaries(text, expected):
    findings = detect([{"segment_id": "s", "text": text}])
    assert len(findings) == expected
    if findings:
        assert text[findings[0]["start"]:findings[0]["end"]] == SYNTHETIC_AWS


@pytest.mark.parametrize("segments", [None, {}, [None], ["synthetic"],
    [{"segment_id": "s", "text": None}], [{"segment_id": "", "text": "synthetic"}]])
def test_malformed_secret_scan_input_has_content_free_error(segments):
    with pytest.raises(ValueError) as failure:
        detect(segments)
    assert str(failure.value) == "invalid segments"


def test_tokens_are_never_joined_across_segments_and_multiple_matches_stay_ordered():
    midpoint = len(SYNTHETIC_OPENAI) // 2
    assert detect([{"segment_id": "a", "text": SYNTHETIC_OPENAI[:midpoint]},
                   {"segment_id": "b", "text": SYNTHETIC_OPENAI[midpoint:]}]) == []
    text = SYNTHETIC_OPENAI + "\nAWS_ACCESS_KEY_ID=" + SYNTHETIC_AWS
    findings = detect([{"segment_id": "a", "text": text}])
    assert [text[item["start"]:item["end"]] for item in findings] == [SYNTHETIC_OPENAI, SYNTHETIC_AWS]
