"""R10/R11/R12: loopback startup settings and synthetic model lifecycle."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from detector.server import ServerSettings, DetectorConfigError, create_server, main, settings_from_env
from detector.tests.test_runtime import FakeBackend, package


TOKEN = "synthetic-detector-server-token"


def environment(path="/synthetic/model/package", **changes):
    return {"PII_DETECTOR_TOKEN": TOKEN, "PII_MODEL_PACKAGE": path} | changes


def test_t10_settings_default_and_secret_path_repr_are_hidden():
    settings = settings_from_env(environment())
    assert settings.port == 8766 and settings.package_dir == Path("/synthetic/model/package")
    assert TOKEN not in repr(settings) and "/synthetic/model/package" not in repr(settings)
    with pytest.raises(Exception):
        settings.port = 1


@pytest.mark.parametrize("changes", [
    {"PII_DETECTOR_TOKEN": ""}, {"PII_DETECTOR_TOKEN": "white space"},
    {"PII_MODEL_PACKAGE": "relative/path"}, {"PII_MODEL_PACKAGE": ""},
    {"PII_DETECTOR_PORT": "0"}, {"PII_DETECTOR_PORT": "65536"}, {"PII_DETECTOR_PORT": "１２３"},
    {"PII_DETECTOR_PORT": "+8766"}, {"PII_DETECTOR_PORT": "8766\n"},
    {"PII_DETECTOR_HOST": "0.0.0.0"}, {"PII_DETECTOR_TLS_DISABLED": "1"},
])
def test_t10_invalid_or_unsupported_environment_fails_without_values(changes):
    with pytest.raises(DetectorConfigError) as caught:
        settings_from_env(environment(**changes))
    assert TOKEN not in str(caught.value) and "relative/path" not in str(caught.value)


@pytest.mark.parametrize("missing", ["PII_DETECTOR_TOKEN", "PII_MODEL_PACKAGE"])
def test_t10_required_environment_missing_is_rejected(missing):
    env = environment()
    del env[missing]
    with pytest.raises(DetectorConfigError):
        settings_from_env(env)


def test_t11_env_entrypoint_without_required_config_exits_two_and_no_traceback():
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PII_DETECTOR_", "PII_MODEL_"))}
    result = subprocess.run([sys.executable, "-m", "detector"], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr and "configuration" in result.stderr


def test_t11_importing_main_module_does_not_start_uvicorn():
    import importlib
    assert callable(importlib.import_module("detector.__main__").main)
    result = subprocess.run([sys.executable, "-c", "import detector.__main__; print('import-only')"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == "import-only"


def test_t11_main_uses_fixed_loopback_binding_and_no_access_log(monkeypatch):
    import detector.server as module
    observed = []
    monkeypatch.setattr(module, "settings_from_env", lambda: settings_from_env(environment(PII_DETECTOR_PORT="8877")))
    monkeypatch.setattr(module.importlib, "import_module", lambda name: SimpleNamespace(run=lambda app, **kwargs: observed.append(kwargs)))
    assert main([]) == 0
    assert observed == [{"host": "127.0.0.1", "port": 8877, "access_log": False}]


def test_t11_cli_flags_cannot_override_host_or_tls(capsys):
    assert main(["--host", "0.0.0.0"]) == 2
    assert "0.0.0.0" not in capsys.readouterr().err


def test_t07_verified_package_starts_warms_and_inspects_with_versions(tmp_path):
    root, _ = package(tmp_path)
    loaded = FakeBackend()
    calls = []
    def loader(path, manifest):
        calls.append(path)
        return loaded
    async def run():
        app = create_server(settings_from_env(environment(str(root))), loader=loader)
        assert not calls
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert loaded.warmed and len(calls) == 1
                assert (await client.get("/readyz")).status_code == 200
                response = await client.post("/v1/inspect", headers={"Authorization": f"Bearer {TOKEN}"}, json={
                    "schema_version": "1.0", "request_id": "r1", "inspection_id": "i1", "direction": "outbound",
                    "segments": [{"segment_id": "s", "text": "synthetic"}]})
                assert response.status_code == 200 and response.json()["model_version"] == "mock-v1"
        async with app.router.lifespan_context(app):
            assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing_package", "warmup", "loader"])
def test_t06_failed_startup_keeps_health_alive_without_fake_readiness(tmp_path, failure, caplog):
    root, _ = package(tmp_path)
    if failure == "missing_package":
        root = root / "absent"
    def loader(path, manifest):
        if failure == "loader":
            raise RuntimeError("SYNTHETIC-RAW-SECRET")
        return FakeBackend(warmup_fails=True)
    async def run():
        app = create_server(settings_from_env(environment(str(root))), loader=loader)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/healthz")).status_code == 200
                response = await client.get("/readyz")
                assert response.status_code == 503 and "SECRET" not in response.text
                assert app.state.model_manager.model_version == "unloaded"
                assert app.state.detector_startup_error == "model_initialization_failed"
    asyncio.run(run())
    assert "SYNTHETIC-RAW-SECRET" not in caplog.text


def test_t06_cancelled_startup_keeps_native_prepare_owned_until_completion(tmp_path):
    root, _ = package(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def loader(path, manifest):
            entered.set()
            await release.wait()
            return FakeBackend()
        app = create_server(settings_from_env(environment(str(root))), loader=loader)
        async def startup():
            async with app.router.lifespan_context(app):
                pytest.fail("cancelled startup cannot become serving")
        pending = asyncio.create_task(startup())
        await entered.wait()
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done() and not app.state.model_manager.ready
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not app.state.model_manager.ready
    asyncio.run(run())


@pytest.mark.parametrize("options", [{"port": True}, {"port": 0}, {"port": 65536},
    {"package_dir": "/string/is/not/Path"}, {"package_dir": Path("relative")},
    {"package_dir": Path("/synthetic/line\nbreak")}, {"token": "한글"}, {"token": None}])
def test_t10_direct_settings_constructor_cannot_bypass_validation(options):
    with pytest.raises(DetectorConfigError):
        ServerSettings(**({"token": TOKEN, "package_dir": Path("/synthetic/package")} | options))


def test_t10_nonstandard_environment_value_types_are_rejected():
    with pytest.raises(DetectorConfigError):
        settings_from_env(environment(PII_DETECTOR_PORT=123))
    with pytest.raises(DetectorConfigError):
        settings_from_env(environment(PII_MODEL_PACKAGE=Path("/synthetic/package")))


def test_t10_settings_from_process_environment_and_extreme_valid_port(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("PII_DETECTOR_", "PII_MODEL_")):
            monkeypatch.delenv(key)
    for key, value in environment(PII_DETECTOR_PORT="65535").items():
        monkeypatch.setenv(key, value)
    assert settings_from_env().port == 65535


def test_t10_dotenv_is_not_implicitly_read(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith(("PII_DETECTOR_", "PII_MODEL_")):
            monkeypatch.delenv(key)
    (tmp_path / ".env").write_text("PII_DETECTOR_TOKEN=PRIVATE-SYNTHETIC\nPII_MODEL_PACKAGE=/private-path\n")
    assert main([]) == 2
    error = capsys.readouterr().err
    assert "PRIVATE-SYNTHETIC" not in error and "/private-path" not in error


@pytest.mark.parametrize("failure,expected", [(RuntimeError("PRIVATE-SYNTHETIC"), 2), (KeyboardInterrupt(), 130)])
def test_t11_runner_failures_and_interrupts_are_safe(monkeypatch, capsys, failure, expected):
    import detector.server as module
    monkeypatch.setattr(module, "settings_from_env", lambda: ServerSettings(TOKEN, Path("/synthetic/package")))
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(module.importlib, "import_module", lambda name: SimpleNamespace(run=fail))
    assert main([]) == expected
    assert "PRIVATE-SYNTHETIC" not in capsys.readouterr().err


def test_t11_server_rejects_arbitrary_configuration_objects():
    with pytest.raises(DetectorConfigError):
        create_server({"token": TOKEN})


def test_t06_default_loader_missing_package_does_not_import_or_download_gliner(tmp_path, monkeypatch):
    import detector.gliner_backend as module
    def forbidden(name):
        pytest.fail("missing package must fail before loading any model library")
    monkeypatch.setattr(module.importlib, "import_module", forbidden)
    async def run():
        app = create_server(ServerSettings(TOKEN, tmp_path / "missing"))
        async with app.router.lifespan_context(app):
            assert not app.state.model_manager.ready
            assert app.state.detector_startup_error == "model_initialization_failed"
    asyncio.run(run())


def test_t06_shutdown_does_not_dispose_backend_owned_by_inflight_request(tmp_path):
    root, _ = package(tmp_path)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class Backend(FakeBackend):
            async def __call__(self, segments):
                entered.set()
                await release.wait()
                return []
        app = create_server(ServerSettings(TOKEN, root), loader=lambda *args: Backend())
        async with app.router.lifespan_context(app):
            pending = asyncio.create_task(app.state.model_manager.inspect([{"segment_id": "s", "text": "synthetic"}]))
            await entered.wait()
        assert not pending.done()
        release.set()
        assert (await pending).status == "complete"
    asyncio.run(run())
