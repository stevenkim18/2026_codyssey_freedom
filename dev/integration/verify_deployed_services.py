"""User-PC HTTPS verifier. Private config only; no event writes or OpenAI calls."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
from urllib.parse import urlsplit

import httpx

from gateway.transport import InspectionClient
from integration.verify_m0_runtime import validate_response

LIMIT = 1024 * 1024


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


class VerificationError(Exception):
    """Fixed codes only; remote bodies, credentials and filesystem paths stay private."""


def require(condition, code="invalid_configuration"):
    if not condition:
        raise VerificationError(code)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def reject(value):
        raise ValueError
    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result
    try:
        require(len(data) <= LIMIT, "response_limit")
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject, parse_float=finite)
        require(isinstance(result, dict), "invalid_json")
        def depth(value, level=0):
            require(level <= 32, "invalid_json")
            if isinstance(value, dict):
                for item in value.values():
                    depth(item, level + 1)
            elif isinstance(value, list):
                for item in value:
                    depth(item, level + 1)
        depth(result)
        return result
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("invalid_json") from None


def _text(value):
    return isinstance(value, str) and bool(value) and not any(ord(char) <= 32 or ord(char) == 127 for char in value)


def _service(service, role):
    require(isinstance(service, dict))
    allowed = {"origin", "token", "ca_file"} | ({"model_version", "threshold_version"} if role == "gpu" else {"health", "ready"})
    require(not set(service) - allowed)
    origin = service.get("origin")
    require(_text(origin) and not any(char in origin for char in "?#\\"))
    parsed = urlsplit(origin)
    require(parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.password is None
            and parsed.path == "" and parsed.port != 0)
    if "ca_file" in service:
        require(isinstance(service["ca_file"], str) and not any(ord(char) < 32 or ord(char) == 127 for char in service["ca_file"])
                and Path(service["ca_file"]).is_absolute())
    if "token" in service:
        require(_text(service["token"]) and all(33 <= ord(char) <= 126 for char in service["token"]))
    if role == "gpu":
        require("token" in service and all(_text(service.get(key)) and service[key] != "unloaded"
                                          for key in ("model_version", "threshold_version")))
    else:
        for name in ("health", "ready"):
            contract = service.get(name)
            require(isinstance(contract, dict) and set(contract) == {"path", "status", "json", "authentication"})
            path = contract["path"]
            require(isinstance(path, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", path) and "//" not in path
                    and not any(part in {".", ".."} for part in path.split("/")))
            require(type(contract["status"]) is int and 200 <= contract["status"] < 300)
            require(isinstance(contract["json"], dict) and contract["authentication"] in {"none", "bearer"})
            require(contract["authentication"] != "bearer" or "token" in service)


def load_config(path):
    try:
        path = Path(path).absolute()
        require(path.parent.name == ".private" and not path.parent.is_symlink())
        directory = path.parent.stat()
        require(stat.S_IMODE(directory.st_mode) == 0o700 and directory.st_uid == os.getuid())
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid())
            data = strict_json(source.read(LIMIT + 1))
        require(not set(data) - {"schema_version", "gpu", "general", "test_only"} and data.get("schema_version") == "1.0")
        require("gpu" in data or "general" in data)
        require(type(data.get("test_only", False)) is bool)
        for role in ("gpu", "general"):
            if role in data:
                _service(data[role], role)
                if data.get("test_only"):
                    require(urlsplit(data[role]["origin"]).hostname in {"127.0.0.1", "localhost", "::1"})
        return data
    except Exception:
        raise VerificationError("invalid_configuration") from None


def tls_context(service):
    try:
        if "ca_file" not in service:
            return ssl.create_default_context()
        path = Path(service["ca_file"])
        require(path.is_absolute() and not path.is_symlink() and path.is_file(), "invalid_ca")
        # Supplying cafile uses this explicitly selected trust anchor set.
        context = ssl.create_default_context(cafile=str(path))
        require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname, "invalid_ca")
        return context
    except Exception:
        raise VerificationError("invalid_ca") from None


async def _json_request(client, method, url, expected_status, *, token=None, body=None):
    response = None
    try:
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        async with asyncio.timeout(10):
            request = client.build_request(method, url, headers=headers, json=body, timeout=10)
            require(str(request.url) == url, "destination_changed")
            response = await client.send(request, stream=True, follow_redirects=False, auth=None)
            require(response.status_code == expected_status, "unexpected_http_status")
            require(response.headers.get("content-encoding", "identity").lower().strip() == "identity", "invalid_encoding")
            require(response.headers.get("content-type", "").split(";", 1)[0].lower().strip() == "application/json", "invalid_json")
            length = response.headers.get("content-length")
            if length is not None:
                require(length.isascii() and length.isdecimal() and int(length) <= LIMIT, "response_limit")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                require(len(content) + len(chunk) <= LIMIT, "response_limit")
                content.extend(chunk)
            return strict_json(bytes(content))
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("https_request_failed") from None
    finally:
        if response is not None:
            await response.aclose()


async def verify_gpu(client, service):
    try:
        _service(service, "gpu")
        origin = service["origin"]
        for path, expected in (("/healthz", "alive"), ("/readyz", "ready")):
            result = await _json_request(client, "GET", origin + path, 200)
            require(result == {"status": expected}, "health_contract_failed")
        invalid_token = secrets.token_urlsafe(24)
        while invalid_token == service["token"]:
            invalid_token = secrets.token_urlsafe(24)
        unauthorized = await _json_request(client, "POST", origin + "/v1/inspect", 401, token=invalid_token, body={})
        require(unauthorized == {"status": "error", "error_code": "unauthorized"}, "authorization_boundary_failed")
        request = {"schema_version": "1.0", "request_id": secrets.token_hex(12), "inspection_id": secrets.token_hex(12),
                   "direction": "outbound", "segments": [{"segment_id": "synthetic", "text": "공개 예시 e\u0301 👩‍💻"}]}
        inspect = InspectionClient(client, origin + "/v1/inspect", service["token"])
        statuses = []
        async def record_status(response):
            statuses.append(response.status_code)
        client.event_hooks["response"].append(record_status)
        try:
            raw = await inspect(request["segments"], request["direction"], request["request_id"], request["inspection_id"])
        finally:
            client.event_hooks["response"].remove(record_status)
        require(statuses == [200], "inspection_http_status_failed")
        result = validate_response(statuses[0], raw, request, service["model_version"], service["threshold_version"])
        return {"health_status": 200, "ready_status": 200, "unauthorized_status": 401, "inspection": result}
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("gpu_verification_failed") from None


async def verify_general(client, service):
    try:
        _service(service, "general")
        result = {"contract_only": True, "event_delivery_verified": False}
        for name in ("health", "ready"):
            contract = service[name]
            token = service.get("token") if contract["authentication"] == "bearer" else None
            data = await _json_request(client, "GET", service["origin"] + contract["path"], contract["status"], token=token)
            require(json.dumps(data, sort_keys=True, allow_nan=False) == json.dumps(contract["json"], sort_keys=True, allow_nan=False),
                    "general_contract_failed")
            result[name + "_status"] = contract["status"]
        return result
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("general_verification_failed") from None


async def verify(config):
    results = {"status": "passed", "test_only": config.get("test_only", False), "tls_verified": True,
               "scope": "HTTPS contract probe; no model quality, performance SLO, GPU placement or event-delivery claim"}
    for role in ("gpu", "general"):
        if role in config:
            context = tls_context(config[role])
            async with httpx.AsyncClient(verify=context, trust_env=False, follow_redirects=False, timeout=10) as client:
                results[role] = await (verify_gpu(client, config[role]) if role == "gpu" else verify_general(client, config[role]))
    return results


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("config", type=Path, help="private JSON under .private (directory0700/file0600)")
    parser.add_argument("--output", type=Path, required=True, help="new evidence JSON; existing files preserved")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        with args.output.open("x") as destination:
            try:
                report = asyncio.run(verify(config))
            except VerificationError as error:
                destination.write(json.dumps({"status": "failed", "error_code": str(error), "tls_verified": False}) + "\n")
                raise
            destination.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print("HTTPS verification passed")
        return 0
    except VerificationError as error:
        print(f"HTTPS verification failed: {error}", file=sys.stderr)
    except Exception:
        print("HTTPS verification failed: verification_failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
