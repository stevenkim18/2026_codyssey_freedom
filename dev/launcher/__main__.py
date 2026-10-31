"""Operator entrypoint for the existing protected Codex profile policy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .profiles import PINNED_CLI, ProfileError, install, launch_arguments, uninstall


class LauncherError(Exception):
    """Only fixed, content-free operational errors are reported."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise LauncherError("invalid_cli_arguments")


def selected_home(value):
    if value is None:
        base = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        path = base / "pii-gateway-home"
    else:
        path = Path(value).expanduser()
    if (not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts
            or any(ord(char) < 32 or ord(char) == 127 for char in str(path))
            or any(parent.is_symlink() for parent in [path, *path.parents])):
        raise LauncherError("invalid_dedicated_home")
    return path


def verified_binary(run):
    executable = shutil.which("codex")
    if not executable:
        raise LauncherError("codex_executable_missing")
    executable = str(Path(executable).absolute())
    try:
        result = run([executable, "--version"], capture_output=True, text=True,
                     timeout=10, check=False, shell=False)
        if result.returncode != 0 or result.stdout.strip() != "codex-cli " + PINNED_CLI:
            raise LauncherError("unsupported_codex_version")
    except LauncherError:
        raise
    except Exception:
        raise LauncherError("codex_version_probe_failed") from None
    return executable


def main(argv=None, *, run=subprocess.run):
    parser = SafeParser(description="Install, run or restore the verified text-only Codex gateway profile")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("install", "run", "uninstall"):
        action = actions.add_parser(name)
        action.add_argument("--codex-home", help="Dedicated directory; default CODEX_HOME/pii-gateway-home or ~/.codex/pii-gateway-home")
        if name == "install":
            action.add_argument("--port", type=int, default=8765)
            action.add_argument("--model", default="gpt-6.1-sol")
        if name == "run":
            action.add_argument("arguments", nargs=argparse.REMAINDER, help="After --, supply exec and supported Codex arguments")
    try:
        options = parser.parse_args(argv)
        home = selected_home(options.codex_home)
        if options.action == "uninstall":
            # No marker means there is no owned installation to recover. Leave
            # any independent/restored profile intact; never guess ownership.
            state = home / ".pii-gateway-install"
            if state.exists() or state.is_symlink():
                uninstall(home)
            print(json.dumps({"status":"uninstalled"}))
            return 0
        executable = verified_binary(run)
        if options.action == "install":
            install(home, port=options.port, model=options.model, cli_version=PINNED_CLI)
            # Verify the resulting profile and immutable instruction companion
            # through the same public policy that an actual run must pass.
            launch_arguments(PINNED_CLI, ["exec", "profile-validation"], codex_home=home)
            print(json.dumps({"status":"installed", "cli_version":PINNED_CLI}))
            return 0
        token = os.environ.get("PII_GATEWAY_LOCAL_TOKEN")
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            raise LauncherError("gateway_local_token_required")
        arguments = options.arguments
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        command = launch_arguments(PINNED_CLI, arguments, codex_home=home)
        command[0] = executable  # execute exactly the binary whose version passed
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(home)
        result = run(command, env=environment, check=False, shell=False)
        return result.returncode if result.returncode >= 0 else 128-result.returncode
    except KeyboardInterrupt:
        return 130
    except LauncherError as exc:
        print(json.dumps({"status":"error", "code":str(exc)}), file=sys.stderr)
        return 2
    except ProfileError:
        print(json.dumps({"status":"error", "code":"protected_profile_refused"}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status":"error", "code":"launcher_operation_failed"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
