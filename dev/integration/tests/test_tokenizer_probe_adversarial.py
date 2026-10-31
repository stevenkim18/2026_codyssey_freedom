"""Independent oracle execution-mode and isolation regressions; no model load."""
import builtins
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from integration import verify_m0_tokenizer as probe


@pytest.mark.parametrize("optimization", ["flag", "environment"])
def test_optimized_python_still_rejects_missing_coverage(optimization):
    command = [sys.executable]
    environment = os.environ.copy()
    if optimization == "flag":
        command.append("-O")
    else:
        environment["PYTHONOPTIMIZE"] = "1"
    command += ["-c", "from integration.verify_m0_tokenizer import check_coverage; check_coverage('complete', ['missing'], [[]], 0, 0)"]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0, "optimized Python accepted omitted nonwhite input"


def test_failed_probe_restores_process_socket_methods(monkeypatch, tmp_path):
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    original_import = builtins.__import__
    def fail_gliner(name, *args, **kwargs):
        if name == "gliner":
            raise ImportError("synthetic dependency failure")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fail_gliner)
    monkeypatch.setattr(probe.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    saved_environment = os.environ.copy()
    try:
        with pytest.raises(ImportError):
            probe.run(tmp_path)
        assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection) == originals
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = originals
        os.environ.clear()
        os.environ.update(saved_environment)
