"""Environment-only loopback entrypoint; actual GPU/TLS deployment is separate."""
import asyncio
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import importlib
import os
from pathlib import Path
import sys

from fastapi import FastAPI

from .app import create_app
from .gliner_backend import load_gliner
from .runtime import ModelManager


class DetectorConfigError(Exception):
    """Content-free startup configuration failure."""
    def __init__(self, code="invalid_configuration"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServerSettings:
    token: str = field(repr=False)
    package_dir: Path = field(repr=False)
    port: int = 8766

    def __post_init__(self):
        if (not isinstance(self.token, str) or not self.token
                or any(not 33 <= ord(char) <= 126 for char in self.token)):
            raise DetectorConfigError()
        if (not isinstance(self.package_dir, Path) or not self.package_dir.is_absolute()
                or any(ord(char) < 32 or ord(char) == 127 for char in str(self.package_dir))):
            raise DetectorConfigError()
        try:
            str(self.package_dir).encode("utf-8")
        except UnicodeError:
            raise DetectorConfigError() from None
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise DetectorConfigError()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        values = os.environ if env is None else env
        permitted = {"PII_DETECTOR_TOKEN", "PII_MODEL_PACKAGE", "PII_DETECTOR_PORT"}
        if any(isinstance(key, str) and key.startswith(("PII_DETECTOR_", "PII_MODEL_")) and key not in permitted for key in values):
            raise DetectorConfigError()
        try:
            token = values["PII_DETECTOR_TOKEN"]
            package = values["PII_MODEL_PACKAGE"]
            raw_port = values.get("PII_DETECTOR_PORT", "8766")
            if (not isinstance(package, str) or not package
                    or not isinstance(raw_port, str) or not raw_port.isascii() or not raw_port.isdecimal()):
                raise ValueError
            return cls(token=token, package_dir=Path(package), port=int(raw_port))
        except (KeyError, TypeError, ValueError):
            raise DetectorConfigError() from None


def settings_from_env(env: Mapping[str, str] | None = None) -> ServerSettings:
    return ServerSettings.from_env(env)


def create_server(settings: ServerSettings, *, loader=load_gliner) -> FastAPI:
    """Build once, initialize once during lifespan, never substitute a fake model.

    Failure leaves the live process unready with a safe state code. Shutdown
    deliberately does not dispose a backend while ModelManager may still own
    native inference work; the host must drain/cancel requests before loop exit.
    """
    if not isinstance(settings, ServerSettings):
        raise DetectorConfigError()
    manager = ModelManager(loader)
    app = create_app(manager=manager, token=settings.token)
    app.state.model_manager = manager
    app.state.detector_startup_error = None
    startup_lock = asyncio.Lock()
    attempted = False

    @asynccontextmanager
    async def lifespan(application):
        nonlocal attempted
        async with startup_lock:
            if not attempted:
                attempted = True
                try:
                    await manager.initialize(settings.package_dir)
                except asyncio.CancelledError:
                    application.state.detector_startup_error = "model_initialization_cancelled"
                    raise
                except Exception:
                    # Raw package paths, model exceptions, and secrets are not logged.
                    application.state.detector_startup_error = "model_initialization_failed"
        yield

    app.router.lifespan_context = lifespan
    return app


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("detector startup refused: invalid_configuration", file=sys.stderr)
        return 2
    try:
        settings = settings_from_env()
    except DetectorConfigError:
        print("detector startup refused: invalid_configuration", file=sys.stderr)
        return 2
    try:
        uvicorn = importlib.import_module("uvicorn")
        uvicorn.run(create_server(settings), host="127.0.0.1", port=settings.port, access_log=False)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("detector startup refused: server_start_failed", file=sys.stderr)
        return 2
