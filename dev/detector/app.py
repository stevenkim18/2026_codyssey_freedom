"""Fail-closed inspection API with one resident inference execution slot.

No default backend is supplied: creating this app does not load or emulate M0.
An injected backend must cover every supplied segment without silent truncation;
it must return BackendResult(status='incomplete') when it cannot do so.
"""
import asyncio
import hmac
import json
import math
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .rules import detect_secrets
from .runtime import ModelManager
from .schemas import BackendResult, Finding, InspectionRequest, InspectionResponse, Segment


Backend = Callable[[list[Segment]], Awaitable[list[Finding | dict] | BackendResult]]
MAX_REQUEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError
    return result


def _invalid_constant(value):
    raise ValueError


def _strict_json(body):
    # Check depth before allocating nested Python objects or invoking recursion.
    depth = 0
    quoted = escaped = False
    for char in body:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError
        elif char in (93, 125):
            depth -= 1
    data = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_float=_finite_float, parse_constant=_invalid_constant)
    if not isinstance(data, dict):
        raise ValueError


class _InspectionBoundary:
    """Authenticate before ASGI receive, then buffer at most one MiB of JSON."""
    def __init__(self, app, token):
        self.app = app
        self.expected = b"Bearer " + token.encode("ascii") if token is not None else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] == "GET" and scope["path"] in {"/healthz", "/readyz"}:
            return await self.app(scope, receive, send)

        async def reject(status, code):
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            await JSONResponse({"status": "error", "error_code": code}, status_code=status, headers=headers)(scope, receive, send)

        headers = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value)
        auth = headers.get(b"authorization", [])
        if self.expected is None or len(auth) != 1 or not hmac.compare_digest(auth[0], self.expected):
            return await reject(401, "unauthorized")
        if scope["method"] != "POST" or scope["path"] != "/v1/inspect":
            return await self.app(scope, receive, send)
        content_type = headers.get(b"content-type", [])
        encoding = headers.get(b"content-encoding", [b"identity"])
        if (len(content_type) != 1 or content_type[0].split(b";", 1)[0].strip().lower() != b"application/json"
                or len(encoding) != 1 or encoding[0].strip().lower() != b"identity"):
            return await reject(400, "invalid_request")
        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
            return await reject(400, "invalid_request")
        try:
            expected_length = int(lengths[0]) if lengths else None
        except ValueError:
            return await reject(400, "invalid_request")
        if expected_length is not None and expected_length > MAX_REQUEST_BYTES:
            return await reject(413, "body_limit")
        buffered = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                return await reject(400, "invalid_request")
            chunk = message.get("body", b"")
            if len(buffered) + len(chunk) > MAX_REQUEST_BYTES:
                return await reject(413, "body_limit")
            buffered.extend(chunk)
            if not message.get("more_body", False):
                break
        if expected_length is not None and expected_length != len(buffered):
            return await reject(400, "invalid_request")
        body = bytes(buffered)
        del buffered
        try:
            _strict_json(body)
        except (ValueError, UnicodeError, RecursionError):
            return await reject(400, "invalid_request")

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        return await self.app(scope, replay, send)


def create_app(*, backend: Backend | None = None, token: str | None = None,
               manager: ModelManager | None = None,
               model_version: str = "unloaded", threshold_version: str = "unloaded",
               max_queue: int = 8, queue_timeout_s: float = 2,
               inspection_timeout_s: float = 10) -> FastAPI:
    if token is not None and (not isinstance(token, str) or not token or any(not 33 <= ord(char) <= 126 for char in token)):
        raise ValueError("invalid inspection token")
    if backend is not None and not callable(backend):
        raise ValueError("invalid inspection backend")
    if manager is not None and (not isinstance(manager, ModelManager) or backend is not None
                                or model_version != "unloaded" or threshold_version != "unloaded"):
        raise ValueError("invalid inspection manager configuration")
    if any(not isinstance(version, str) or not version.strip() for version in (model_version, threshold_version)):
        raise ValueError("invalid inspection versions")
    if isinstance(max_queue, bool) or not isinstance(max_queue, int) or max_queue < 0:
        raise ValueError("max_queue must be a nonnegative integer")
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0
           for value in (queue_timeout_s, inspection_timeout_s)):
        raise ValueError("timeouts must be positive")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(_InspectionBoundary, token=token)
    slot = asyncio.Semaphore(1)
    admitted = 0
    tasks: set[asyncio.Task] = set()

    def is_ready():
        if manager is not None:
            return manager.ready and bool(token)
        return (backend is not None and bool(token)
                and all(isinstance(version, str) and version.strip() and version != "unloaded"
                        for version in (model_version, threshold_version)))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Never serialize pydantic input/error context: it can contain original text.
        return JSONResponse({"status": "error", "error_code": "invalid_request"}, status_code=400)

    @app.get("/healthz")
    async def health():
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        ready_now = is_ready()
        return JSONResponse({"status": "ready" if ready_now else "not_ready"}, status_code=200 if ready_now else 503)

    @app.post("/v1/inspect")
    async def inspect(body: InspectionRequest):
        nonlocal admitted
        started = time.monotonic()
        timing = {"queue": 0.0, "inference": 0.0, "rules": 0.0, "total": 0.0}
        request_model_version = manager.model_version if manager is not None else model_version
        request_threshold_version = manager.threshold_version if manager is not None else threshold_version
        original_segments = tuple((segment.segment_id, segment.text) for segment in body.segments)

        def response(status="error", findings=None, code=None, http_status=200):
            timing["total"] = (time.monotonic() - started) * 1000
            result = InspectionResponse(request_id=body.request_id, inspection_id=body.inspection_id,
                status=status, findings=findings or [], model_version=request_model_version,
                threshold_version=request_threshold_version, timing_ms=timing["total"], timing_breakdown_ms=timing, error_code=code)
            return JSONResponse(result.model_dump(exclude_none=True), status_code=http_status)

        if sum(len(segment.text) for segment in body.segments) > 16000:
            return response(code="text_limit", http_status=413)
        if not is_ready():
            return response(code="not_ready", http_status=503)
        if admitted >= 1 + max_queue:
            return response(code="queue_full", http_status=429)
        admitted += 1
        try:
            await asyncio.wait_for(slot.acquire(), timeout=min(queue_timeout_s, inspection_timeout_s))
        except TimeoutError:
            admitted -= 1
            if time.monotonic() - started >= inspection_timeout_s:
                return response(code="inspection_timeout", http_status=504)
            return response(code="queue_timeout", http_status=429)
        except BaseException:
            admitted -= 1
            raise
        timing["queue"] = (time.monotonic() - started) * 1000

        async def invoke_backend():
            if manager is not None:
                return await manager.inspect_with_metadata(body.segments)
            return await backend(body.segments)

        task = asyncio.create_task(invoke_backend())
        tasks.add(task)

        def release(finished):
            nonlocal admitted
            tasks.discard(finished)
            admitted -= 1
            slot.release()
            # Consume failures without emitting raw backend exception messages.
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(release)
        inference_started = time.monotonic()
        try:
            remaining = max(0.0, inspection_timeout_s - (time.monotonic() - started))
            done, _ = await asyncio.wait({task}, timeout=remaining)
            timing["inference"] = (time.monotonic() - inference_started) * 1000
            if not done:
                task.cancel()
                return response(code="inspection_timeout", http_status=504)
            if task.cancelled():
                return response(code="backend_cancelled", http_status=503)
            raw = task.result()
            if manager is not None:
                raw, request_model_version, request_threshold_version = raw
            if tuple((segment.segment_id, segment.text) for segment in body.segments) != original_segments:
                raise ValueError("backend mutated original text")
            result = BackendResult.model_validate(raw) if isinstance(raw, BackendResult) else BackendResult(findings=raw)
            if result.status != "complete":
                return response(status="incomplete", code="incomplete_inspection")
            lengths = {segment.segment_id: len(segment.text) for segment in body.segments}
            for finding in result.findings:
                if finding.segment_id not in lengths or finding.end > lengths[finding.segment_id]:
                    raise ValueError("invalid backend span")
            rules_started = time.monotonic()
            findings = result.findings + detect_secrets(body.segments)
            timing["rules"] = (time.monotonic() - rules_started) * 1000
            return response(status="complete", findings=findings)
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            return response(code="inspection_failed", http_status=503)

    return app
