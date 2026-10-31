import asyncio
import json

from integration import verify_m0_runtime as probe
from detector.server import ServerSettings, create_server
from detector.tests.test_runtime import FakeBackend, package


def test_plan_never_loads_model_or_creates_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(probe, "run", lambda *a: (_ for _ in ()).throw(AssertionError("model loaded")))
    assert probe.main([str(tmp_path / "absent"), "--device", "cpu", "--plan"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["executed_inspections"] == 0
    assert report["planned_inspections"] == 2
    assert report["inspection_deadline_seconds"] == 10
    assert list(tmp_path.iterdir()) == []


def test_rss_units_are_explicit(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    assert probe.rss_evidence(100)["high_water_bytes"] == 100
    monkeypatch.setattr(probe.sys, "platform", "linux")
    assert probe.rss_evidence(100)["high_water_bytes"] == 102400
    monkeypatch.setattr(probe.sys, "platform", "unknown")
    assert probe.rss_evidence(100)["high_water_bytes"] is None


def test_internal_observer_is_separate_from_public_metrics(tmp_path):
    root, manifest = package(tmp_path)
    app = create_server(ServerSettings(token="probe-token", package_dir=root.resolve()), loader=lambda *a: FakeBackend())
    report = asyncio.run(probe.exercise_app(app, "probe-token", manifest["model_version"], manifest["threshold_version"]))
    assert report["warmup_seconds"] is None
    assert report["executed_inspections"] == 2
    for inspection in report["inspections"]:
        assert inspection["internal_timing"]["stages"]["queue"]["completed"] == 1
        assert "timing_breakdown_ms" in inspection
        assert "text" not in inspection
