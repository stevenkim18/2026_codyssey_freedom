"""Authenticated loopback HTTP boundary; no raw upstream bytes are streamed."""
from __future__ import annotations

import asyncio
import hmac
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .engine import GatewayError
from .responses import safe_events


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("nonfinite JSON")


class SafeResponse(Response):
    def __init__(self, result, gateway, state, stream):
        self.result, self.gateway, self.state = result, gateway, state
        self.stream = stream
        content = b"" if stream else json.dumps(result.body, ensure_ascii=False, allow_nan=False).encode()
        super().__init__(content, media_type="text/event-stream" if stream else "application/json",
                         headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                  "X-Request-ID": result.request_id})
        if stream:
            self.raw_headers = [(k, v) for k, v in self.raw_headers if k != b"content-length"]

    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        if self.stream:
            for chunk in safe_events(self.result.body):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await send({"type": "http.response.body", "body": self.body, "more_body": False})
        # "delivered" here means the ASGI server accepted all safe bytes. TCP
        # delivery and CLI rendering are not independently acknowledged.
        try:
            self.gateway.record_delivery(self.result, delivered=True)
        except GatewayError:
            # The durable unknown record remains; refuse further work until repair.
            self.state.audit_healthy = False


def create_app(gateway, *, token, allowed_hosts=("127.0.0.1", "localhost", "::1"), allowed_origins=()):
    if not isinstance(token, str) or not token:
        raise ValueError("local gateway token required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.audit_healthy = True

    @app.exception_handler(GatewayError)
    async def safe_error(request, exc):
        return JSONResponse({"error": {"type": "gateway_error", "code": exc.code,
                                      "message": f"Protected gateway rejected the request: {exc.code}."}},
                            status_code=exc.status, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def health():
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        return JSONResponse({"status": "ready" if app.state.audit_healthy else "not_ready"},
                            status_code=200 if app.state.audit_healthy else 503)

    @app.post("/v1/responses")
    async def responses(request: Request):
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise GatewayError("UNAUTHORIZED", 401)
        if request.url.hostname not in allowed_hosts:
            raise GatewayError("HOST_NOT_ALLOWED", 403)
        if request.headers.get("origin") is not None and request.headers["origin"] not in allowed_origins:
            raise GatewayError("ORIGIN_NOT_ALLOWED", 403)
        if not app.state.audit_healthy:
            raise GatewayError("JOURNAL_UNAVAILABLE")
        if (request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
                or request.headers.get("content-encoding", "identity").lower() != "identity"):
            raise GatewayError("UNSUPPORTED_ENCODING", 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 1024 * 1024:
                raise GatewayError("REQUEST_TOO_LARGE", 413)
            data.extend(chunk)
        try:
            body = json.loads(data, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
        except (ValueError, RecursionError, UnicodeError):
            raise GatewayError("INVALID_JSON", 400) from None

        async def disconnected():
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    return

        worker = asyncio.create_task(gateway.handle(body))
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({worker, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                raise GatewayError("CLIENT_DISCONNECTED", 499)
            result = await worker
            gateway.record_delivery(result, delivered=False)
            return SafeResponse(result, gateway, app.state, isinstance(body, dict) and body.get("stream", False))
        finally:
            watcher.cancel()
            if not worker.done():
                worker.cancel()
            await asyncio.gather(watcher, worker, return_exceptions=True)

    return app
