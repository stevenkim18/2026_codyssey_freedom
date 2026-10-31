"""Production-shaped local composition; no implicit fake model or API fallback."""
from contextlib import asynccontextmanager
import os
import sys

from fastapi import FastAPI
import httpx
import uvicorn

from .app import create_app
from .engine import Gateway
from .journal import Journal, JournalError
from .settings import Settings, SettingsError
from .transport import InspectionClient, TransportError, UpstreamClient


def create_server(settings, *, client_factory=httpx.AsyncClient):
    @asynccontextmanager
    async def lifespan(app):
        with Journal(settings.state_dir / "events.sqlite3") as journal:
            async with client_factory(trust_env=False, verify=True) as detector_http, \
                    client_factory(trust_env=False, verify=True) as upstream_http:
                inspector = InspectionClient(detector_http, settings.detector_url, settings.detector_token)
                upstream = UpstreamClient(upstream_http, "https://api.openai.com/v1/responses",
                                          settings.openai_api_key, {settings.model_id})
                gateway = Gateway(inspector, upstream, journal, allowed_models={settings.model_id})
                app.mount("/", create_app(gateway, token=settings.local_token))
                yield

    return FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def main():
    try:
        settings = Settings.from_env(os.environ)
        app = create_server(settings)
    except (SettingsError, JournalError, TransportError):
        print("Gateway startup rejected: invalid or unavailable secure configuration.", file=sys.stderr)
        return 2
    # No command-line or environment switch can open the binding or access logs.
    uvicorn.run(app, host="127.0.0.1", port=settings.port, access_log=False, log_level="critical")
    return 0
