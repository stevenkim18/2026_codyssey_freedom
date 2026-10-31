"""Read-only local diagnostics; never establish remote detector readiness."""
import http.client
import json
import os

from .profiles import PINNED_CLI, launch_arguments


def diagnose(home, verify_cli, gateway_url=None):
    report = {"status": "checked", "installed": (home / ".pii-gateway-install").is_dir(),
              "profile_verified": False, "cli_verified": False,
              "local_gateway_reachable": "unverified", "local_gateway_authorized": "unverified",
              "remote_detector_readiness": "unverified"}
    try:
        verify_cli()
        report["cli_verified"] = True
    except Exception:
        report["code"] = "codex_verification_failed"
    try:
        arguments = launch_arguments(PINNED_CLI, ["exec", "profile-validation"], codex_home=home)
        # Use the validated snapshot rather than rereading potentially changed files.
        prefix = "model_providers.pii-gateway.base_url="
        endpoint = next(json.loads(item[len(prefix):]) for item in arguments if item.startswith(prefix))
        report["profile_verified"] = True
    except Exception:
        report["code"] = "protected_profile_refused"
        return 2, report
    if not report["cli_verified"]:
        return 2, report
    if gateway_url is None:
        return 0, report
    if gateway_url != endpoint.removesuffix("/v1"):
        report["code"] = "gateway_url_refused"
        return 2, report
    token = os.environ.get("PII_GATEWAY_LOCAL_TOKEN", "")
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        report["code"] = "gateway_local_token_required"
        return 2, report
    # Exact validated IPv4 literal bypasses DNS, proxies, and redirects.
    port = int(gateway_url.rsplit(":", 1)[1])
    report["local_gateway_reachable"] = False
    report["local_gateway_authorized"] = False

    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read(4097)
            if len(data) > 4096:
                raise ValueError
            return response.status, json.loads(data)
        finally:
            connection.close()

    try:
        status, body = request("GET", "/healthz")
        if status != 200 or body != {"status": "alive"}:
            raise ValueError
        report["local_gateway_reachable"] = True
        # Negative control proves authentication precedes parsing. A service
        # with auth disabled must not satisfy the positive malformed-JSON probe.
        status, body = request("POST", "/v1/responses", b"{",
                               {"Content-Type": "application/json"})
        if status != 401 or body.get("error", {}).get("code") != "UNAUTHORIZED":
            report["code"] = "gateway_authorization_unverified"
            return 2, report
        # Invalid JSON is rejected after auth, before any detector/provider call.
        status, body = request("POST", "/v1/responses", b"{",
                               {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        if status != 400 or body.get("error", {}).get("code") != "INVALID_JSON":
            report["code"] = "gateway_authorization_unverified"
            return 2, report
        report["local_gateway_authorized"] = True
        return 0, report
    except Exception:
        report["code"] = "gateway_probe_failed"
        return 2, report
