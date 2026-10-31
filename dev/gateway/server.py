"""Production-shaped local composition; no implicit fake model or API fallback."""
from contextlib import asynccontextmanager
import os
import sys

from fastapi import FastAPI
import httpx
import uvicorn
from starlette.routing import Mount

from .app import create_app
from .delivery import LocalEventShipper
from .engine import Gateway
from .journal import Journal, JournalError
from .replay import ReplayWorker
from .settings import Settings, SettingsError
from .tls import detector_tls_context
from .transport import InspectionClient, TransportError, UpstreamClient


def create_server(settings, *, client_factory=httpx.AsyncClient, send_events=None, replay_options=None):
    """Compose local resources, with event replay disabled unless injected.

    send_events is an explicit async adapter returning the existing BatchReceipt
    after durable central confirmation. Its transport lifecycle belongs to the
    caller; it receives event metadata only, never this server's HTTP clients or
    journal. replay_options configures ReplayWorker scheduling, not a C codec.
    """
    if send_events is None and replay_options is not None:
        raise ValueError("event replay requires an explicit adapter")
    if replay_options is not None and not isinstance(replay_options, dict):
        raise ValueError("invalid event replay configuration")
    detector_verify = detector_tls_context(settings.detector_ca_file)
    shipper = LocalEventShipper(None, send_events) if send_events is not None else None
    worker = ReplayWorker(shipper, **dict(replay_options or {})) if shipper is not None else None
    lifespan_active = False

    @asynccontextmanager
    async def lifespan(app):
        nonlocal lifespan_active
        if lifespan_active:
            raise RuntimeError("gateway lifespan already active")
        lifespan_active = True
        try:
            with Journal(settings.state_dir / "events.sqlite3") as journal:
                async with client_factory(trust_env=False, verify=detector_verify) as detector_http, \
                        client_factory(trust_env=False, verify=True) as upstream_http:
                    inspector = InspectionClient(detector_http, settings.detector_url, settings.detector_token)
                    upstream = UpstreamClient(upstream_http, "https://api.openai.com/v1/responses",
                                              settings.openai_api_key, {settings.model_id})
                    gateway = Gateway(inspector, upstream, journal, allowed_models={settings.model_id})
                    mounted = Mount("/", app=create_app(gateway, token=settings.local_token))
                    app.router.routes.append(mounted)
                    try:
                        if worker is not None:
                            # Retain the shipper across lifespans: a previous
                            # cancellation-resistant send must still drain before
                            # another attempt can use this newly opened journal.
                            shipper.journal = journal
                            await worker.start()
                        yield
                    finally:
                        try:
                            if worker is not None:
                                await worker.stop()
                        finally:
                            app.router.routes.remove(mounted)
        finally:
            lifespan_active = False

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.replay_worker = worker
    return app


def main():
    try:
        settings = Settings.from_env(os.environ)
        app = create_server(settings)
        # No command-line or environment switch can open the binding or access logs.
        uvicorn.run(app, host="127.0.0.1", port=settings.port, access_log=False, log_level="critical")
    except SystemExit as exc:
        if exc.code in (None, 0):
            return 0
        print("Gateway startup rejected: invalid or unavailable secure configuration.", file=sys.stderr)
        return 2
    except (SettingsError, JournalError, TransportError, OSError):
        print("Gateway startup rejected: invalid or unavailable secure configuration.", file=sys.stderr)
        return 2
    return 0
