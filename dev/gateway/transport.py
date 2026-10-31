"""Bounded, authenticated HTTP adapters; raw remote errors never escape.

No retries or redirects are performed. Callers own policy and the local journal;
an ambiguous upstream failure must never trigger automatic retransmission.
"""
import asyncio
import json
from urllib.parse import urlsplit

import httpx

from detector.schemas import InspectionRequest, InspectionResponse


MAX_RESPONSE_BYTES = 1024 * 1024
INSPECTION_TIMEOUT_S = 10.0
UPSTREAM_TIMEOUT_S = 120.0
_UPSTREAM_URL = "https://api.openai.com/v1/responses"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class TransportError(Exception):
    """Content-free transport failure; ``code`` is safe for a local event."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _credential(value):
    if (not isinstance(value, str) or not value
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise TransportError("invalid_credential")
    return value


def _destination(url, *, kind, allow_loopback_http):
    try:
        if (not isinstance(url, str) or not url or "?" in url or "#" in url or "\\" in url
                or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
            raise ValueError
        parsed = urlsplit(url)
        # Accessing port explicitly detects invalid ranges and malformed ports.
        port = parsed.port
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path != ("/v1/inspect" if kind == "inspection" else "/v1/responses")
                or port == 0):
            raise ValueError
        loopback_http = (allow_loopback_http is True and parsed.scheme == "http"
                         and parsed.hostname in _LOOPBACK)
        if kind == "upstream":
            if url != _UPSTREAM_URL and not loopback_http:
                raise ValueError
        elif parsed.scheme != "https" and not loopback_http:
            raise ValueError
    except (ValueError, TypeError):
        raise TransportError("invalid_destination") from None
    return url


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError


async def _request_json(client, url, token, body, *, kind, timeout, allow_loopback_http):
    response = None
    try:
        async with asyncio.timeout(timeout):
            request = client.build_request("POST", url, json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                         "Accept-Encoding": "identity", "Content-Type": "application/json"}, timeout=timeout)
            # Client-level default query parameters must not alter the destination.
            _destination(str(request.url), kind=kind, allow_loopback_http=allow_loopback_http)
            response = await client.send(request, stream=True, follow_redirects=False, auth=None)
            if not 200 <= response.status_code < 300:
                raise TransportError(f"{kind}_http_error")
            if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise TransportError(f"{kind}_unsupported_encoding")
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise TransportError(f"{kind}_invalid_response")
            length = response.headers.get("content-length")
            if length is not None:
                if not length.isascii() or not length.isdecimal():
                    raise TransportError(f"{kind}_invalid_response")
                if int(length) > MAX_RESPONSE_BYTES:
                    raise TransportError(f"{kind}_response_limit")
            data = bytearray()
            # Encoding is identity, so no decompression can expand this boundary.
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise TransportError(f"{kind}_response_limit")
                data.extend(chunk)
            result = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if not isinstance(result, dict):
                raise ValueError
            return result
    except TransportError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        raise TransportError(f"{kind}_timeout") from None
    except Exception:
        raise TransportError(f"{kind}_invalid_response") from None
    finally:
        if response is not None:
            try:
                await response.aclose()
            except Exception:
                # Cleanup failures must not replace a safe error with raw text.
                pass


class InspectionClient:
    def __init__(self, client: httpx.AsyncClient, url, token, *, allow_loopback_http=False):
        self.client = client
        self.url = _destination(url, kind="inspection", allow_loopback_http=allow_loopback_http)
        self.token = _credential(token)
        self.allow_loopback_http = allow_loopback_http

    async def __call__(self, segments, direction, request_id, inspection_id):
        try:
            request = InspectionRequest(schema_version="1.0", request_id=request_id,
                inspection_id=inspection_id, direction=direction, segments=segments)
            if sum(len(segment.text) for segment in request.segments) > 16000:
                raise ValueError
        except Exception:
            raise TransportError("inspection_invalid_request") from None
        raw = await _request_json(self.client, self.url, self.token, request.model_dump(), kind="inspection",
                                 timeout=INSPECTION_TIMEOUT_S, allow_loopback_http=self.allow_loopback_http)
        try:
            result = InspectionResponse.model_validate(raw)
            if (result.request_id != request_id or result.inspection_id != inspection_id
                    or not result.model_version.strip() or not result.threshold_version.strip()
                    or result.model_version == "unloaded" or result.threshold_version == "unloaded"):
                raise ValueError
            lengths = {segment.segment_id: len(segment.text) for segment in request.segments}
            if any(finding.segment_id not in lengths or finding.end > lengths[finding.segment_id] for finding in result.findings):
                raise ValueError
            return result.model_dump(exclude_none=True)
        except Exception:
            raise TransportError("inspection_invalid_response") from None


class UpstreamClient:
    def __init__(self, client: httpx.AsyncClient, url, api_key, allowed_models: set, *, allow_loopback_http=False):
        self.client = client
        self.url = _destination(url, kind="upstream", allow_loopback_http=allow_loopback_http)
        self.api_key = _credential(api_key)
        if (not isinstance(allowed_models, (set, frozenset)) or not allowed_models
                or any(not isinstance(model, str) or not model.strip() for model in allowed_models)):
            raise TransportError("invalid_model_allowlist")
        self.allowed_models = frozenset(allowed_models)
        self.allow_loopback_http = allow_loopback_http

    async def __call__(self, body):
        if (not isinstance(body, dict) or not isinstance(body.get("model"), str)
                or body["model"] not in self.allowed_models):
            raise TransportError("upstream_invalid_request")
        return await _request_json(self.client, self.url, self.api_key, body | {"stream": False}, kind="upstream",
                                   timeout=UPSTREAM_TIMEOUT_S, allow_loopback_http=self.allow_loopback_http)
