"""Verified local-package lifecycle; does not download weights or implement M0.

Manifest contract is exported by package_schema(). Packages contain manifest.json,
inline label_mapping/thresholds/preprocessing and SHA-256-pinned artifact paths.
The supplied loader must use local files only and return an inference callable
with warmup(), or the manager must receive an explicit warmup(backend) callback.
Sync loader/warmup/inference functions run outside the asyncio event loop.
"""
import asyncio
import copy
import hashlib
import inspect
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .schemas import BackendResult, PIIType, Segment, StrictModel


Text = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S(?:.*\S)?$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class ModelIdentity(StrictModel):
    repo_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    revision: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class Preprocessing(StrictModel):
    version: Text
    normalization: Literal["none"]
    offset_unit: Literal["unicode_codepoint"]
    max_input_codepoints: int = Field(ge=1, le=16000)
    overflow: Literal["reject"]


class Artifacts(StrictModel):
    weights: list[Text] = Field(min_length=1)
    config: Text
    tokenizer: list[Text] = Field(min_length=1)
    license: Text
    evaluation: Text


class ModelPackage(StrictModel):
    schema_version: Literal["1.0"]
    model_version: Text
    threshold_version: Text
    model: ModelIdentity
    label_mapping: dict[PIIType, Text] = Field(min_length=1)
    thresholds: dict[PIIType, Score] = Field(min_length=1)
    preprocessing: Preprocessing
    license_id: Text
    artifacts: Artifacts
    files: dict[str, Digest] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self):
        if (self.model_version == "unloaded" or self.threshold_version == "unloaded"
                or set(self.label_mapping) != set(self.thresholds)
                or set(self.label_mapping) & {"API_KEY", "PRIVATE_KEY"}
                or len(set(self.label_mapping.values())) != len(self.label_mapping)):
            raise ValueError("invalid model metadata")
        required = (self.artifacts.weights + self.artifacts.tokenizer + [self.artifacts.config,
                    self.artifacts.license, self.artifacts.evaluation])
        if any(path not in self.files for path in required):
            raise ValueError("unhashed artifact")
        for name in self.files:
            path = PurePosixPath(name)
            if (not name or str(path) != name or path.is_absolute() or ".." in path.parts
                    or "\\" in name or ":" in name or name == "manifest.json"
                    or any(ord(char) <= 32 or ord(char) == 127 for char in name)):
                raise ValueError("invalid package path")
        return self


def package_schema():
    schema = ModelPackage.model_json_schema()
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:ai-pii-gateway:model-package:1.0", "x-owner": "B", **schema}


class ModelRuntimeError(Exception):
    """Only content-free codes escape package/loader/backend boundaries."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _verify_package(package):
    try:
        supplied = Path(package)
        if supplied.is_symlink():
            raise ValueError
        root = supplied.resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        for path in root.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError
        with (root / "manifest.json").open("rb") as handle:
            content = handle.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError
        raw = json.loads(content, object_pairs_hook=_object)
        manifest = ModelPackage.model_validate(raw)
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        if actual != set(manifest.files) | {"manifest.json"}:
            raise ValueError
        for name, expected in manifest.files.items():
            digest = hashlib.sha256()
            with (root / name).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError
        return root, manifest.model_dump()
    except Exception:
        raise ModelRuntimeError("invalid_model_package") from None


async def _settle(task):
    """Retain ownership until a worker exits, including repeated cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()


async def _call(function, *args):
    if inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(getattr(function, "__call__", None)):
        return await function(*args)
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Thread cancellation cannot stop native/GPU work; preserve the drain.
        await _settle(worker)
        raise
    return await result if inspect.isawaitable(result) else result


class ModelManager:
    def __init__(self, loader, *, warmup=None):
        if not callable(loader) or (warmup is not None and not callable(warmup)):
            raise ModelRuntimeError("invalid_model_loader")
        self._loader = loader
        self._warmup = warmup
        self._backend = None
        self._manifest = None
        self._ready = False
        self._active = 0
        self._drained = asyncio.Event()
        self._drained.set()
        self._replacement = asyncio.Lock()

    @property
    def ready(self):
        return self._ready

    @property
    def model_version(self):
        return self._manifest["model_version"] if self._manifest else "unloaded"

    @property
    def threshold_version(self):
        return self._manifest["threshold_version"] if self._manifest else "unloaded"

    async def initialize(self, package):
        await self.replace(package)

    async def _prepare(self, package):
        root, manifest = await asyncio.to_thread(_verify_package, package)
        backend = await _call(self._loader, root, copy.deepcopy(manifest))
        inference = backend if callable(backend) else getattr(backend, "inspect", None)
        if not callable(inference):
            raise ModelRuntimeError("invalid_model_backend")
        warmup = self._warmup or getattr(backend, "warmup", None)
        if not callable(warmup):
            raise ModelRuntimeError("model_warmup_required")
        warmed = await _call(warmup, backend) if self._warmup else await _call(warmup)
        if warmed is False:
            raise ModelRuntimeError("model_warmup_failed")
        # Detect package changes during load/warmup before publishing readiness.
        _, rechecked = await asyncio.to_thread(_verify_package, root)
        if manifest != rechecked:
            raise ModelRuntimeError("invalid_model_package")
        return inference, manifest

    async def replace(self, package):
        async with self._replacement:
            previous_ready = self._ready
            self._ready = False
            preparing = None
            try:
                await self._drained.wait()
                preparing = asyncio.create_task(self._prepare(package))
                backend, manifest = await asyncio.shield(preparing)
            except asyncio.CancelledError:
                # A synchronous loader may still own GPU work. Do not restore
                # readiness until that worker actually exits.
                if preparing is not None:
                    await _settle(preparing)
                self._ready = previous_ready
                raise
            except Exception:
                self._ready = previous_ready
                raise ModelRuntimeError("model_replacement_failed") from None
            self._backend, self._manifest = backend, manifest
            self._ready = True

    async def inspect(self, segments):
        result, _, _ = await self.inspect_with_metadata(segments)
        return result

    async def inspect_with_metadata(self, segments):
        """Return the result and versions captured with its selected backend."""
        if not self._ready:
            raise ModelRuntimeError("model_not_ready")
        backend, manifest = self._backend, self._manifest
        self._active += 1
        self._drained.clear()
        try:
            validated = [Segment.model_validate(segment) for segment in segments]
            ids = [segment.segment_id for segment in validated]
            if not validated or len(ids) != len(set(ids)):
                raise ValueError
            if sum(len(segment.text) for segment in validated) > manifest["preprocessing"]["max_input_codepoints"]:
                raise ValueError
            original = tuple((segment.segment_id, segment.text) for segment in validated)
            raw = await _call(backend, validated)
            if tuple((segment.segment_id, segment.text) for segment in validated) != original:
                raise ValueError
            result = BackendResult.model_validate(raw) if isinstance(raw, BackendResult) else BackendResult(findings=raw)
            lengths = {segment.segment_id: len(segment.text) for segment in validated}
            if any(finding.segment_id not in lengths or finding.end > lengths[finding.segment_id] for finding in result.findings):
                raise ValueError
            result = result if result.status == "complete" else BackendResult(status="incomplete")
            return result, manifest["model_version"], manifest["threshold_version"]
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ModelRuntimeError("model_inference_failed") from None
        finally:
            self._active -= 1
            if not self._active:
                self._drained.set()
