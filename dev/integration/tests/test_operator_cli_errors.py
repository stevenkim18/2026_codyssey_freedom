"""Fresh operator-CLI argument failures must not echo accidental secrets."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module,arguments", [
    ("deployment", ["preflight", "--role", "gpu"]),
    ("integration.verify_deployed_services", ["synthetic-config", "--output", "unused.json"]),
    ("integration.verify_m0_runtime", ["synthetic-package", "--device", "cpu", "--output", "unused.json"]),
    ("integration.verify_m0_tokenizer", ["synthetic-package"]),
])
def test_invalid_operator_arguments_never_echo_secret(module, arguments, tmp_path):
    sentinel = "SYNTHETIC_OPERATOR_SECRET"
    result = subprocess.run([sys.executable, "-m", module, *arguments, "--token", sentinel],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert sentinel not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("module,arguments", [
    ("deployment", ["SYNTHETIC_OPERATOR_SECRET"]),
    ("deployment", ["preflight", "--role", "SYNTHETIC_OPERATOR_SECRET"]),
    ("integration.verify_m0_runtime", ["synthetic-package", "--device", "SYNTHETIC_OPERATOR_SECRET", "--output", "unused.json"]),
])
def test_invalid_operator_choice_never_echoes_value(module, arguments):
    result = subprocess.run([sys.executable, "-m", module, *arguments],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=15)
    assert result.returncode == 2
    assert "SYNTHETIC_OPERATOR_SECRET" not in result.stdout + result.stderr
    assert "invalid_cli_arguments" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
