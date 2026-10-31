"""Independent deployment child-process prerequisite checks; no SSH or GPU."""
import sys

import pytest

from deployment.remote import RemoteError, application_probe


def test_optimized_child_must_reject_wrong_dependency(monkeypatch):
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    spec = {"python": sys.executable, "role": "general", "device": "cpu",
            "requirements": {"pytest": "0.0.0-deliberately-invalid"}}
    with pytest.raises(RemoteError, match="application_environment_probe_failed"):
        application_probe(spec)
