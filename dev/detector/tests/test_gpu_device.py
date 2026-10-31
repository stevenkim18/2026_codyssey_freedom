"""R03/R11, T06/T07 synthetic device gates; no GPU performance claim."""
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
import detector.gliner_backend as module
import detector.server as server
from detector.tests.test_gliner_backend import Model, manifest, local_package
from detector.tests.test_detector_server import TOKEN, environment
from detector.tests.test_runtime import FakeBackend, package

@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_device_configuration(device):
    assert server.settings_from_env(environment(PII_DETECTOR_DEVICE=device)).device == device
    assert server.settings_from_env(environment()).device == "cpu"

@pytest.mark.parametrize("device", ["", "CUDA", "cuda:0", "mps", "auto", "cpu\n", None, True, []])
def test_invalid_device_is_sanitized(device):
    with pytest.raises(server.DetectorConfigError, match="^invalid_configuration$"):
        server.settings_from_env(environment(PII_DETECTOR_DEVICE=device))
    with pytest.raises(server.DetectorConfigError, match="^invalid_configuration$"):
        server.ServerSettings(TOKEN, Path("/package"), device=device)

@pytest.mark.asyncio
async def test_server_passes_cuda_to_loader(tmp_path):
    root, _ = package(tmp_path)
    calls = []
    def loader(path, manifest, *, device):
        calls.append(device)
        return FakeBackend()
    app = server.create_server(server.ServerSettings(TOKEN, root, device="cuda"), loader=loader)
    async with app.router.lifespan_context(app):
        assert app.state.model_manager.ready and calls == ["cuda"]

def fake_cuda(monkeypatch, available=True):
    original = module.importlib.import_module
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available, current_device=lambda: 0))
    monkeypatch.setattr(module.importlib, "import_module", lambda name: torch if name == "torch" else original(name))

def tensor(kind="cuda", index=0):
    return SimpleNamespace(device=SimpleNamespace(type=kind, index=index))

@pytest.mark.parametrize("placement", ["cpu_parameter", "cpu_buffer", "wrong_index", "empty", "uninspectable"])
def test_cuda_requires_all_parameters_and_buffers_on_selected_device(local_package, monkeypatch, placement):
    fake_cuda(monkeypatch)
    model = Model()
    model.parameters = lambda: iter([] if placement == "empty" else [tensor("cpu" if placement == "cpu_parameter" else "cuda", 1 if placement == "wrong_index" else 0)])
    model.buffers = lambda: iter([tensor("cpu" if placement == "cpu_buffer" else "cuda")])
    if placement == "uninspectable":
        model.parameters = None
    with pytest.raises(module.ModelBackendError, match="^gliner_load_failed$"):
        module.load_gliner(*local_package, device="cuda", loader=lambda *args, **kwargs: model)

def test_cuda_correct_placement_and_loader_contract(local_package, monkeypatch):
    fake_cuda(monkeypatch)
    model = Model()
    model.parameters = lambda: iter([tensor()])
    model.buffers = lambda: iter([tensor()])
    seen = []
    def loader(path, **kwargs):
        seen.append(kwargs)
        return model
    loaded = module.load_gliner(*local_package, device="cuda", loader=loader)
    assert loaded.model is model and not loaded.warmed
    assert seen[0]["map_location"] == "cuda" and seen[0]["trust_remote_code"] is False

@pytest.mark.asyncio
async def test_cuda_unavailable_never_loads_or_publishes_readiness(local_package, monkeypatch, caplog):
    fake_cuda(monkeypatch, available=False)
    calls = []
    def loader(path, manifest, *, device):
        return module.load_gliner(path, manifest, device=device, loader=lambda *args, **kwargs: calls.append("loaded") or Model())
    app = server.create_server(server.ServerSettings(TOKEN, local_package[0], device="cuda"), loader=loader)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/readyz")
            assert response.status_code == 503
            assert app.state.detector_startup_error == "model_initialization_failed"
            assert not app.state.model_manager.ready and not calls
            assert str(local_package[0]) not in response.text
    assert str(local_package[0]) not in caplog.text


@pytest.mark.asyncio
async def test_cuda_loader_failure_remains_unready_without_serializing_exception(local_package, monkeypatch, caplog):
    fake_cuda(monkeypatch)
    def failing_loader(*args, **kwargs):
        raise RuntimeError("SYNTHETIC-CUDA-SECRET")
    def loader(path, manifest, *, device):
        return module.load_gliner(path, manifest, device=device, loader=failing_loader)
    app = server.create_server(server.ServerSettings(TOKEN, local_package[0], device="cuda"), loader=loader)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/healthz")).status_code == 200
            response = await client.get("/readyz")
            assert response.status_code == 503
            assert "SYNTHETIC-CUDA-SECRET" not in response.text
            assert not app.state.model_manager.ready
            assert app.state.model_manager.model_version == "unloaded"
            assert app.state.detector_startup_error == "model_initialization_failed"
    assert "SYNTHETIC-CUDA-SECRET" not in caplog.text
