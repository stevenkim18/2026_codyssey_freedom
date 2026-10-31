"""Bounded, no-authentication SSH diagnostics; never changes a server.

Only fixed failure stages, route interface facts and TCP state names are saved.
Neither a timeout nor a route interface identifies a particular remote fault.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .core import load_access


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def classify(returncode, stderr):
    text = stderr.lower()
    connected = "connection established" in text
    phase, code = "ssh", "unclassified"
    if "operation not permitted" in text:
        phase, code = "local_permissions", "denied"
    elif "could not resolve hostname" in text:
        phase, code = "resolution", "failed"
    elif "no route to host" in text:
        phase, code = "routing", "no_route"
    elif "remote host identification has changed" in text:
        phase, code, connected = "host_key", "changed", True
    elif "host key verification failed" in text:
        phase, code, connected = "host_key", "unverified", True
    elif "permission denied (" in text:
        phase, code, connected = "authentication", "not_attempted", True
    elif "authenticated to " in text or returncode == 0:
        phase, code, connected = "authentication", "unexpected_none_authentication", True
    elif "timed out" in text:
        phase, code = ("ssh_banner" if connected else "tcp_connect"), "timeout"
    elif "connection refused" in text:
        phase, code = "tcp_connect", "refused"
    return {"phase":phase, "code":code, "tcp_established":connected}


def command_for(access, known_hosts):
    ip = str(ipaddress.ip_address(access["private_ip"]))
    user, port = access["username"], access["ssh_port"]
    known = str(Path(known_hosts).absolute())
    if (not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", user)
            or type(port) is not int or not 1 <= port <= 65535
            or any(ord(c) < 32 or ord(c) == 127 or c in {'"', '\\', '%', '$'} for c in known)):
        raise ValueError("invalid_diagnostic_configuration")
    return ["ssh", "-vv", "-N", "-F", "/dev/null", "-o", "BatchMode=yes",
            "-o", "NumberOfPasswordPrompts=0", "-o", "PreferredAuthentications=none",
            "-o", "PubkeyAuthentication=no", "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no", "-o", "GSSAPIAuthentication=no",
            "-o", "HostbasedAuthentication=no", "-o", "StrictHostKeyChecking=yes",
            "-o", 'UserKnownHostsFile="'+known+'"', "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "KnownHostsCommand=none", "-o", "VerifyHostKeyDNS=no", "-o", "UpdateHostKeys=no",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
            "-p", str(port), user+"@"+ip]


def route_observation(target, default):
    def fields(text):
        return dict(line.strip().split(":", 1) for line in text.splitlines() if ":" in line)
    given, baseline = fields(target), fields(default)
    interface = given.get("interface", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", interface):
        return {"available":False}
    same = (given.get("gateway", "").strip() == baseline.get("gateway", "").strip()
            and interface == baseline.get("interface", "").strip()
            and bool(given.get("gateway", "").strip()))
    return {"interface":interface, "matches_default_route":same}


def _local_output(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=2, check=False)
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _terminate_owned(process):
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=2)


def diagnose_one(access, known_hosts):
    arguments = command_for(access, known_hosts)
    environment = dict(os.environ, LC_ALL="C")
    started = time.monotonic()
    process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=environment)
    states = []
    try:
        time.sleep(0.2)  # One observation while the owned connection is pending.
        table = _local_output(["netstat", "-an", "-p", "tcp"])
        endpoint = access["private_ip"]+"."+str(access["ssh_port"])
        for line in table.splitlines():
            fields = line.split()
            if endpoint in fields and fields[-1] in {"SYN_SENT", "ESTABLISHED", "CLOSED", "CLOSE_WAIT"}:
                states.append(fields[-1])
        try:
            _, error = process.communicate(timeout=max(0.1, 8-(time.monotonic()-started)))
        except subprocess.TimeoutExpired:
            _, error = _terminate_owned(process)
            error += "\nTimed out"
    finally:
        if process.poll() is None:
            _terminate_owned(process)
    report = classify(process.returncode, error)
    target = _local_output(["route", "-n", "get", access["private_ip"]]) if sys.platform == "darwin" else ""
    default = _local_output(["route", "-n", "get", "default"]) if sys.platform == "darwin" else ""
    return report | {"socket_states":sorted(set(states)),
                     "socket_observation_scope":"single matching-endpoint snapshot; not PID-bound; empty is inconclusive",
                     "route":route_observation(target, default),
                     "seconds":round(time.monotonic()-started, 3), "password_authentication_attempted":False}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--role", choices=("gpu", "general", "all"), default="all")
    parser.add_argument("--output", type=Path, required=True, help="New sanitized JSON evidence file")
    arguments = parser.parse_args(argv)
    try:
        private = Path(__file__).resolve().parents[1] / ".private"
        roles = ("gpu", "general") if arguments.role == "all" else (arguments.role,)
        access = {role:load_access(private/"server-access.local.json", role) for role in roles}
        with arguments.output.open("x", encoding="utf-8") as evidence:
            try:
                result = {"scope":"connection stages and local route observations; remote root cause unconfirmed",
                          "servers":{role:diagnose_one(value, private/"known_hosts") for role, value in access.items()}}
            except Exception:
                result = {"status":"failed", "code":"ssh_diagnostic_failed"}
                evidence.write(json.dumps(result)+"\n")
                print(json.dumps(result))
                return 2
            evidence.write(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result, indent=2))
        return 0 if all(item["tcp_established"] for item in result["servers"].values()) else 1
    except Exception:
        print(json.dumps({"status":"failed", "code":"ssh_diagnostic_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
