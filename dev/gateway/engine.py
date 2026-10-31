"""Bidirectional transmission control independent of HTTP and model runtime."""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from uuid import uuid4

from .journal import JournalError
from .policy import Decision, evaluate
from .responses import ProtocolError, parse_request, parse_response


def _require_active():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class GatewayError(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass(frozen=True)
class SafeResult:
    body: dict
    request_id: str
    inspection_id: str
    decision: Decision
    model_version: str
    started: float


class Gateway:
    def __init__(self, inspector, upstream, journal, *, allowed_models,
                 client_id=None, session_id=None, max_inflight=8):
        if type(max_inflight) is not int or max_inflight < 1:
            raise ValueError("invalid gateway capacity")
        self.inspector, self.upstream, self.journal = inspector, upstream, journal
        self.allowed_models = frozenset(allowed_models)
        self.client_id, self.session_id = client_id or str(uuid4()), session_id or str(uuid4())
        self.max_inflight, self._inflight = max_inflight, 0

    def _audit(self, request_id, inspection_id, direction, decision, model_version, started,
               *, stage="decision", delivery_status="not_sent", error_code=None):
        counts = Counter(f["type"] for f in decision.findings)
        event = {"schema_version": "1.0", "event_id": str(uuid4()), "client_id": self.client_id,
            "session_id": self.session_id, "request_id": request_id, "inspection_id": inspection_id,
            "revision": 1, "occurred_at": datetime.now(timezone.utc).isoformat(),
            "direction": direction, "stage": stage, "decision": decision.action,
            "delivery_status": delivery_status, "findings": [{"type": k, "count": v} for k, v in sorted(counts.items())],
            "rule_ids": decision.rule_ids, "model_version": model_version,
            "policy_version": "default-v1", "timing_ms": (time.monotonic() - started) * 1000,
            "error_code": error_code}
        try:
            self.journal.append(event)
        except Exception:
            raise GatewayError("JOURNAL_UNAVAILABLE") from None

    async def _inspect(self, segments, direction, request_id, started, expected_version=None, expected_threshold=None):
        inspection_id, version = str(uuid4()), "unknown"
        try:
            async with asyncio.timeout(10) as deadline:
                inspection = await self.inspector(segments, direction, request_id, inspection_id)
                _require_active()
                if deadline.expired():
                    raise GatewayError("INSPECTION_FAILED")
            if (not isinstance(inspection, dict) or inspection.get("schema_version") != "1.0"
                    or inspection.get("request_id") != request_id or inspection.get("inspection_id") != inspection_id):
                raise GatewayError("INSPECTION_IDENTITY")
            decision = evaluate(segments, inspection)
            if decision.action == "error":
                raise GatewayError("INSPECTION_INVALID")
            version = inspection["model_version"]
            if expected_version is not None and version != expected_version:
                raise GatewayError("MODEL_CHANGED")
            if expected_threshold is not None and inspection["threshold_version"] != expected_threshold:
                raise GatewayError("MODEL_CHANGED")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, GatewayError) else "INSPECTION_FAILED"
            self._audit(request_id, inspection_id, direction, Decision("error"), "unknown", started, error_code=code)
            raise GatewayError(code) from None
        if decision.action == "mask":
            # Exactly one further inspection; no recursive replacement loop.
            try:
                recheck_id = str(uuid4())
                async with asyncio.timeout(10) as deadline:
                    recheck = await self.inspector(decision.segments, direction, request_id, recheck_id)
                    _require_active()
                    if deadline.expired():
                        raise GatewayError("REINSPECTION_FAILED")
                if (not isinstance(recheck, dict) or recheck.get("schema_version") != "1.0"
                        or recheck.get("request_id") != request_id or recheck.get("inspection_id") != recheck_id
                        or recheck.get("model_version") != version
                        or recheck.get("threshold_version") != inspection["threshold_version"]
                        or evaluate(decision.segments, recheck).action != "allow"):
                    raise GatewayError("REINSPECTION_FAILED")
            except asyncio.CancelledError:
                raise
            except Exception:
                self._audit(request_id, inspection_id, direction, Decision("error"), version, started,
                            error_code="REINSPECTION_FAILED")
                raise GatewayError("REINSPECTION_FAILED") from None
        self._audit(request_id, inspection_id, direction, decision, version, started)
        if decision.action == "block":
            raise GatewayError("POLICY_BLOCK", 403)
        return decision, version, inspection["threshold_version"], inspection_id

    async def handle(self, body):
        if self._inflight >= self.max_inflight:
            raise GatewayError("GATEWAY_BUSY", 429)
        self._inflight += 1
        request_id, started = str(uuid4()), time.monotonic()
        try:
            try:
                parsed = parse_request(body, self.allowed_models)
            except ProtocolError:
                self._audit(request_id, str(uuid4()), "outbound", Decision("error"), "unknown", started,
                            error_code="UNSUPPORTED_PROTOCOL")
                raise GatewayError("UNSUPPORTED_PROTOCOL", 400) from None
            decision, version, threshold, inspection_id = await self._inspect(parsed.segments, "outbound", request_id, started)
            _require_active()
            approved = parsed.replace(decision.segments)
            # A durable unknown result exists BEFORE dispatch, including crash windows.
            self._audit(request_id, inspection_id, "outbound", decision, version, started,
                        stage="delivery", delivery_status="unknown")
            try:
                async with asyncio.timeout(120) as deadline:
                    upstream_response = await self.upstream(approved)
                    _require_active()
                    if deadline.expired():
                        raise GatewayError("UPSTREAM_FAILED")
            except asyncio.CancelledError:
                raise
            except Exception:
                raise GatewayError("UPSTREAM_FAILED") from None
            self._audit(request_id, inspection_id, "outbound", decision, version, started, stage="delivery",
                        delivery_status="forwarded_sanitized" if decision.action == "mask" else "forwarded_original")
            try:
                parsed_response = parse_response(upstream_response)
            except ProtocolError:
                self._audit(request_id, str(uuid4()), "inbound", Decision("error"), version, started,
                            error_code="UNSUPPORTED_OUTPUT")
                raise GatewayError("UNSUPPORTED_OUTPUT") from None
            inbound, inbound_version, _, inbound_id = await self._inspect(
                parsed_response.segments, "inbound", request_id, started, version, threshold)
            safe_body = parsed_response.replace(inbound.segments)
            safe_body["id"] = f"resp_{request_id.replace('-', '')}"
            safe_body["model"] = approved["model"]
            return SafeResult(safe_body, request_id, inbound_id, inbound, inbound_version, started)
        finally:
            self._inflight -= 1

    def record_delivery(self, result, *, delivered):
        self._audit(result.request_id, result.inspection_id, "inbound", result.decision,
                    result.model_version, result.started, stage="delivery",
                    delivery_status="delivered" if delivered else "unknown")
