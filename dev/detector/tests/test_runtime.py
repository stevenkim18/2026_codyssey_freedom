"""R07/R11/T07 lifecycle/package checks with synthetic files and no GPU."""
import asyncio
import hashlib
import json
import threading
from pathlib import Path

import pytest

from detector.runtime import ModelManager, ModelRuntimeError, package_schema
from detector.schemas import BackendResult


def package(tmp_path, version="mock-v1"):
    root = tmp_path / version
    root.mkdir()
    files = {"weights.bin": b"synthetic-weights", "config.json": b"{}", "tokenizer.json": b"{}",
             "LICENSE": b"Synthetic test license", "evaluation.json": b'{"synthetic": true}'}
    for name, content in files.items():
        (root / name).write_bytes(content)
    manifest = {
        "schema_version": "1.0", "model_version": version, "threshold_version": version + "-thresholds",
        "model": {"repo_id": "synthetic/model", "revision": "a" * 40},
        "label_mapping": {"PERSON": "person", "EMAIL_ADDRESS": "email address"},
        "thresholds": {"PERSON": 0.5, "EMAIL_ADDRESS": 0.6},
        "preprocessing": {"version": "synthetic-v1", "normalization": "none", "offset_unit": "unicode_codepoint",
                          "max_input_codepoints": 16000, "overflow": "reject"},
        "license_id": "Apache-2.0",
        "artifacts": {"weights": ["weights.bin"], "config": "config.json", "tokenizer": ["tokenizer.json"],
                      "license": "LICENSE", "evaluation": "evaluation.json"},
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, manifest


def save(root, manifest):
    (root / "manifest.json").write_text(json.dumps(manifest))


class FakeBackend:
    def __init__(self, *, warmup_fails=False):
        self.warmed = False
        self.warmup_fails = warmup_fails

    async def warmup(self):
        if self.warmup_fails:
            raise RuntimeError("SYNTHETIC-RAW-SECRET")
        self.warmed = True

    async def __call__(self, segments):
        assert self.warmed
        return BackendResult(findings=[])


def test_t07_schema_is_locked_to_checked_in_contract():
    published = Path(__file__).parents[2] / "contracts" / "model-package.schema.json"
    assert json.loads(published.read_text()) == package_schema()


def test_t07_initial_unready_and_successful_initialization(tmp_path):
    root, manifest = package(tmp_path)
    backend = FakeBackend()
    calls = []
    def loader(path, incoming):
        assert path == root.resolve()
        assert incoming == manifest
        calls.append(path)
        return backend
    async def run():
        manager = ModelManager(loader)
        assert not manager.ready
        assert manager.model_version == "unloaded"
        with pytest.raises(ModelRuntimeError):
            await manager.inspect([{"segment_id": "s", "text": "synthetic"}])
        await manager.initialize(root)
        assert manager.ready and backend.warmed
        assert manager.model_version == "mock-v1"
        assert manager.threshold_version == "mock-v1-thresholds"
        assert (await manager.inspect([{"segment_id": "s", "text": "😀synthetic"}])).status == "complete"
        assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"}, {"unexpected": "secret"}, {"model_version": ""},
    {"model": {"repo_id": "synthetic/model", "revision": "main"}},
    {"label_mapping": {"UNKNOWN": "person"}}, {"thresholds": {"PERSON": True}},
    {"thresholds": {"PERSON": float("nan"), "EMAIL_ADDRESS": 0.5}},
    {"license_id": ""}, {"preprocessing": {"version": "x", "normalization": "NFC", "offset_unit": "utf16", "max_input_codepoints": 16000, "overflow": "truncate"}},
])
def test_t07_manifest_rejects_unlocked_unknown_or_invalid_fields(tmp_path, change):
    root, manifest = package(tmp_path)
    save(root, manifest | change)
    async def run():
        manager = ModelManager(lambda *args: pytest.fail("invalid package reached loader"))
        with pytest.raises(ModelRuntimeError) as caught:
            await manager.initialize(root)
        assert "secret" not in str(caught.value)
        assert not manager.ready
    asyncio.run(run())


@pytest.mark.parametrize("attack", ["hash", "missing", "unlisted", "traversal", "absolute", "symlink", "directory_symlink", "artifact_missing"])
def test_t07_all_artifacts_verified_and_paths_confined(tmp_path, attack):
    root, manifest = package(tmp_path)
    if attack == "hash":
        (root / "weights.bin").write_bytes(b"tampered")
    elif attack == "missing":
        (root / "weights.bin").unlink()
    elif attack == "unlisted":
        (root / "unverified.bin").write_bytes(b"unknown")
    elif attack in ("traversal", "absolute"):
        manifest["files"]["../outside" if attack == "traversal" else "/tmp/outside"] = "a" * 64
    elif attack == "symlink":
        (root / "weights.bin").unlink()
        (root / "weights.bin").symlink_to(root / "LICENSE")
    elif attack == "directory_symlink":
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    else:
        manifest["artifacts"]["evaluation"] = "absent.json"
    save(root, manifest)
    async def run():
        manager = ModelManager(lambda *args: pytest.fail("invalid package reached loader"))
        with pytest.raises(ModelRuntimeError):
            await manager.initialize(root)
        assert not manager.ready
    asyncio.run(run())


def test_t07_warmup_failure_restores_previous_backend_and_versions(tmp_path):
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    async def run():
        manager = ModelManager(lambda path, manifest: FakeBackend(warmup_fails=manifest["model_version"] == "new"))
        await manager.initialize(old)
        with pytest.raises(ModelRuntimeError) as caught:
            await manager.replace(new)
        assert "SYNTHETIC-RAW-SECRET" not in str(caught.value)
        assert manager.ready and manager.model_version == "old"
        assert manager.threshold_version == "old-thresholds"
        assert (await manager.inspect([{"segment_id": "s", "text": "synthetic"}])).status == "complete"
    asyncio.run(run())


def test_t07_replacement_drains_requests_and_rejects_new_until_warmed(tmp_path):
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        warmup_entered = asyncio.Event()
        warmup_release = asyncio.Event()
        observed = []
        class Backend(FakeBackend):
            def __init__(self, version):
                super().__init__()
                self.version = version
            async def warmup(self):
                if self.version == "new":
                    warmup_entered.set()
                    await warmup_release.wait()
                await super().warmup()
            async def __call__(self, segments):
                observed.append(self.version)
                if self.version == "old":
                    entered.set()
                    await release.wait()
                return BackendResult()
        async def loader(path, manifest):
            return Backend(manifest["model_version"])
        manager = ModelManager(loader)
        await manager.initialize(old)
        existing = asyncio.create_task(manager.inspect([{"segment_id": "s", "text": "synthetic"}]))
        await entered.wait()
        swapping = asyncio.create_task(manager.replace(new))
        await asyncio.sleep(0)
        assert not manager.ready
        with pytest.raises(ModelRuntimeError):
            await manager.inspect([{"segment_id": "s", "text": "new"}])
        assert not warmup_entered.is_set()
        release.set()
        await existing
        await warmup_entered.wait()
        assert not manager.ready and manager.model_version == "old"
        warmup_release.set()
        await swapping
        assert manager.ready and manager.model_version == "new"
        await manager.inspect([{"segment_id": "s", "text": "new"}])
        assert observed == ["old", "new"]
    asyncio.run(run())


def test_t07_warmup_is_mandatory_and_explicit_callback_is_supported(tmp_path):
    root, _ = package(tmp_path)
    async def backend(segments):
        return []
    async def run():
        manager = ModelManager(lambda *args: backend)
        with pytest.raises(ModelRuntimeError):
            await manager.initialize(root)
        assert not manager.ready
        warmed = []
        manager = ModelManager(lambda *args: backend, warmup=lambda loaded: warmed.append(loaded))
        await manager.initialize(root)
        assert manager.ready and warmed == [backend]
    asyncio.run(run())


def test_t11_cancelled_sync_inference_keeps_replacement_blocked_until_worker_exits(tmp_path):
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    entered, release = threading.Event(), threading.Event()
    async def run():
        loaded = []
        class SyncBackend:
            def warmup(self):
                pass
            def __call__(self, segments):
                entered.set()
                release.wait(2)
                return []
        def loader(path, manifest):
            loaded.append(manifest["model_version"])
            return SyncBackend()
        manager = ModelManager(loader)
        await manager.initialize(old)
        pending = asyncio.create_task(manager.inspect([{"segment_id": "s", "text": "synthetic"}]))
        await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        swapping = asyncio.create_task(manager.replace(new))
        try:
            await asyncio.sleep(0.02)
            assert loaded == ["old"]
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await swapping
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"model_version": "unloaded"}, {"threshold_version": "unloaded"},
    {"thresholds": {"PERSON": 0.5}},
    {"label_mapping": {"API_KEY": "key"}, "thresholds": {"API_KEY": 0.5}},
    {"label_mapping": {"PERSON": "same", "EMAIL_ADDRESS": "same"}},
])
def test_t07_metadata_consistency_guards(tmp_path, change):
    root, manifest = package(tmp_path)
    save(root, manifest | change)
    async def run():
        with pytest.raises(ModelRuntimeError):
            await ModelManager(lambda *args: FakeBackend()).initialize(root)
    asyncio.run(run())


@pytest.mark.parametrize("attack", ["root_symlink", "file_root", "missing_root", "duplicate_key", "oversized", "empty_dir", "nested"])
def test_t07_package_structure_is_safe(tmp_path, attack):
    root, manifest = package(tmp_path)
    expected_valid = attack in {"empty_dir", "nested"}
    if attack == "root_symlink":
        link = tmp_path / "link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif attack == "file_root":
        root = root / "weights.bin"
    elif attack == "missing_root":
        root = tmp_path / "missing"
    elif attack == "duplicate_key":
        (root / "manifest.json").write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    elif attack == "oversized":
        (root / "manifest.json").write_text(" " * (1024 * 1024 + 1))
    elif attack == "empty_dir":
        (root / "empty").mkdir()
    else:
        (root / "nested").mkdir()
        (root / "nested" / "config.json").write_bytes(b"{}")
        manifest["files"]["nested/config.json"] = hashlib.sha256(b"{}").hexdigest()
        save(root, manifest)
    async def run():
        manager = ModelManager(lambda *args: FakeBackend())
        if expected_valid:
            await manager.initialize(root)
            assert manager.ready
        else:
            with pytest.raises(ModelRuntimeError):
                await manager.initialize(root)
    asyncio.run(run())


@pytest.mark.parametrize("loader,warmup", [(None, None), (lambda: None, 1)])
def test_t07_invalid_callbacks_are_rejected(loader, warmup):
    with pytest.raises(ModelRuntimeError):
        ModelManager(loader, warmup=warmup)


@pytest.mark.parametrize("mode", ["no_backend", "warmup_false", "mutated_manifest", "tampered_file", "loader_failure"])
def test_t07_failed_preparation_never_sets_ready(tmp_path, mode):
    root, manifest = package(tmp_path)
    class Backend(FakeBackend):
        async def warmup(self):
            if mode == "warmup_false":
                return False
            if mode == "mutated_manifest":
                save(root, manifest | {"threshold_version": "changed"})
            if mode == "tampered_file":
                (root / "weights.bin").write_bytes(b"tampered")
    def loader(path, manifest):
        if mode == "loader_failure":
            raise RuntimeError("SYNTHETIC-RAW-SECRET")
        return None if mode == "no_backend" else Backend()
    async def run():
        manager = ModelManager(loader)
        with pytest.raises(ModelRuntimeError) as caught:
            await manager.initialize(root)
        assert not manager.ready
        assert "SYNTHETIC-RAW-SECRET" not in str(caught.value)
        assert manager.threshold_version == "unloaded"
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["empty", "duplicate", "limit", "invalid_span", "incomplete", "list", "inspect_method"])
def test_t07_inference_boundary_and_alternate_backend_forms(tmp_path, mode):
    root, _ = package(tmp_path)
    class Backend:
        def warmup(self):
            pass
        def inspect(self, segments):
            if mode == "invalid_span":
                return [{"segment_id": "s", "type": "PERSON", "start": 0, "end": 10000, "score": 0.9, "detector": "model"}]
            return BackendResult(status="incomplete") if mode == "incomplete" else []
    async def run():
        backend = Backend()
        if mode != "inspect_method":
            async def inference(segments):
                return backend.inspect(segments)
            inference.warmup = backend.warmup
            loaded = inference
        else:
            loaded = backend
        manager = ModelManager(lambda *args: loaded)
        await manager.initialize(root)
        segments = [{"segment_id": "s", "text": "synthetic"}]
        if mode == "empty":
            segments = []
        elif mode == "duplicate":
            segments *= 2
        elif mode == "limit":
            segments[0]["text"] = "a" * 16001
        if mode in {"empty", "duplicate", "limit", "invalid_span"}:
            with pytest.raises(ModelRuntimeError):
                await manager.inspect(segments)
        else:
            result = await manager.inspect(segments)
            assert result.status == ("incomplete" if mode == "incomplete" else "complete")
    asyncio.run(run())


@pytest.mark.parametrize("fail_after_cancel", [False, True])
def test_t07_cancelled_replacement_waits_for_prepare_and_restores_old(tmp_path, fail_after_cancel):
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def loader(path, manifest):
            if manifest["model_version"] == "new":
                entered.set()
                await release.wait()
                if fail_after_cancel:
                    raise RuntimeError("SYNTHETIC-RAW-SECRET")
            return FakeBackend()
        manager = ModelManager(loader)
        await manager.initialize(old)
        pending = asyncio.create_task(manager.replace(new))
        await entered.wait()
        pending.cancel()
        await asyncio.sleep(0)
        assert not manager.ready and not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert manager.ready and manager.model_version == "old"
    asyncio.run(run())
