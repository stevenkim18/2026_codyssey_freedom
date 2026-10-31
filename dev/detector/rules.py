"""Typed service wrapper for the single shared secret-rule implementation."""
from gateway.secret_rules import detect

from .schemas import Finding, Segment


def detect_secrets(segments: list[Segment]) -> list[Finding]:
    return [Finding.model_validate(finding) for finding in detect([segment.model_dump() for segment in segments])]
