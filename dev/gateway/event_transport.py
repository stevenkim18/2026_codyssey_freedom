"""R08 metadata-only HTTP sender; intentionally NO default C wire protocol.

An adopted C-owned codec must provide synchronous encode(events) -> JSON object
or array, and decode(response_json) -> BatchReceipt AFTER its durable receipt
contract is established. The codec is trusted integration code, not remote code.
An HTTP success alone never implies durable acknowledgement. This module never
touches a journal and is not wired into production until a codec is adopted.

The owned HTTPX client verifies TLS, ignores ambient proxies/authentication, and
does not retry or redirect. _test_transport is solely a unit-test seam; injecting
it does not provide TLS/network verification evidence.
"""
import asyncio
import json
import math
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import httpx

from .delivery import BatchReceipt
from .journal import _validate_event

MAX_BYTES = 1024 * 1024


class EventTransportError(Exception):
    """Content-free codes; never expose remote bodies, credentials or paths."""


def _require(condition, code):
    if not condition:
        raise EventTransportError(code)


def _active():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def _json(data):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def invalid(value):
        raise ValueError
    def finite(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError
        return parsed
    _require(len(data) <= MAX_BYTES, "event_size_limit")
    result = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    _require(type(result) in (dict, list), "event_invalid_json")
    def depth(value, level=0):
        _require(level <= 32, "event_invalid_json")
        if isinstance(value, dict):
            for item in value.values():
                depth(item, level + 1)
        elif isinstance(value, list):
            for item in value:
                depth(item, level + 1)
    depth(result)
    return result


def _serialize(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    _json(encoded)  # Applies the same finite/depth/UTF-8/size boundary to requests.
    return encoded


class EventHTTPTransport:
    def __init__(self, url, token, *, codec, ca_file=None, allow_loopback_http=False,
                 timeout_s=10.0, _test_transport=None):
        try:
            _require(isinstance(url, str) and not any(char in url for char in "?#\\")
                     and not any(ord(char) <= 32 or ord(char) == 127 for char in url), "event_invalid_destination")
            parsed = urlsplit(url)
            loopback = allow_loopback_http is True and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            _require(parsed.hostname and parsed.port != 0 and parsed.username is None and parsed.password is None
                     and parsed.path == "/v1/events/batch" and (parsed.scheme == "https" or loopback), "event_invalid_destination")
            _require(isinstance(token, str) and token and all(33 <= ord(char) <= 126 for char in token), "event_invalid_credential")
            _require(callable(getattr(codec, "encode", None)) and callable(getattr(codec, "decode", None)), "event_codec_required")
            _require(type(timeout_s) in (int, float) and math.isfinite(timeout_s) and 0 < timeout_s <= 10,
                     "event_invalid_timeout")
            if ca_file is not None:
                path = Path(ca_file)
                _require(path.is_absolute() and path.is_file() and not path.is_symlink(), "event_invalid_ca")
                context = ssl.create_default_context(cafile=str(path))
            else:
                context = ssl.create_default_context()
            self._client = httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False,
                                             timeout=timeout_s, transport=_test_transport)
            self._url, self._token, self._codec, self._timeout = url, token, codec, timeout_s
        except EventTransportError:
            raise
        except Exception:
            raise EventTransportError("event_configuration_failed") from None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        try:
            await self._client.aclose()
        except Exception:
            raise EventTransportError("event_close_failed") from None

    async def send_batch(self, events):
        response = None
        try:
            _active()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout
            _require(type(events) is list and 1 <= len(events) <= 100, "event_invalid_batch")
            for event in events:
                _validate_event(event)
            # Snapshot metadata before the trusted codec can mutate its copy.
            safe_events = _json(_serialize(events))
            for event in safe_events:
                _validate_event(event)
            requested = tuple(event["event_id"] for event in safe_events)
            _require(len(set(requested)) == len(requested), "event_invalid_batch")
            body = _serialize(self._codec.encode(safe_events))
            _require(loop.time() < deadline, "event_timeout")
            async with asyncio.timeout_at(deadline):
                request = self._client.build_request("POST", self._url, content=body,
                    headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
                             "Accept": "application/json", "Accept-Encoding": "identity"}, timeout=self._timeout)
                _require(str(request.url) == str(httpx.URL(self._url)), "event_destination_changed")
                response = await self._client.send(request, stream=True, follow_redirects=False, auth=None)
                _require(200 <= response.status_code < 300, "event_http_error")
                types = response.headers.get_list("content-type")
                encodings = response.headers.get_list("content-encoding")
                lengths = response.headers.get_list("content-length")
                _require(len(types) == 1 and types[0].split(";", 1)[0].strip().lower() == "application/json", "event_invalid_response")
                _require(not encodings or len(encodings) == 1 and encodings[0].strip().lower() == "identity", "event_invalid_response")
                _require(len(lengths) <= 1, "event_invalid_response")
                expected = None
                if lengths:
                    _require(lengths[0].isascii() and lengths[0].isdecimal(), "event_invalid_response")
                    expected = int(lengths[0])
                    _require(expected <= MAX_BYTES, "event_size_limit")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    _require(len(content) + len(chunk) <= MAX_BYTES, "event_size_limit")
                    content.extend(chunk)
                _require(expected is None or expected == len(content), "event_invalid_response")
                raw = _json(bytes(content))
                receipt = self._codec.decode(raw)
                _require(type(receipt) is BatchReceipt, "event_invalid_receipt")
                acknowledged = receipt.acknowledged_ids
                _require(type(acknowledged) in (list, tuple) and len(acknowledged) <= len(requested), "event_invalid_receipt")
                _require(all(type(item) is str and item in requested for item in acknowledged), "event_invalid_receipt")
                _require(len(set(acknowledged)) == len(acknowledged), "event_invalid_receipt")
                # A synchronous codec cannot be preempted; reject its late result.
                _require(loop.time() < deadline, "event_timeout")
                _active()
                return BatchReceipt(tuple(acknowledged))
        except asyncio.CancelledError:
            raise
        except EventTransportError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise EventTransportError("event_timeout") from None
        except Exception:
            raise EventTransportError("event_send_failed") from None
        finally:
            if response is not None:
                try:
                    await response.aclose()
                except Exception:
                    pass
