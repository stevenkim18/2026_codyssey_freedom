"""Loopback-only dashboard server: one page and one read-only JSON feed."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit


HOST = "127.0.0.1"
DEFAULT_PORT = 8787
PAGE = Path(__file__).with_name("static") / "index.html"
_CSP = ("default-src 'none'; script-src 'nonce-{0}'; style-src 'nonce-{0}'; connect-src 'self'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # A web page that rebinds its hostname to loopback must not read the feed.
        if self.headers.get("Host") not in self.server.allowed_hosts:
            self._send(403, "text/plain; charset=utf-8", b"forbidden")
            return
        path = urlsplit(self.path).path
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            body = self.server.page.replace("__NONCE__", nonce).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body, {"Content-Security-Policy": _CSP.format(nonce)})
        elif path == "/api/feed":
            body = json.dumps(self.server.feed.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send(self, status, content_type, body, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, feed, port=DEFAULT_PORT):
        self.page = PAGE.read_text(encoding="utf-8")  # A plugin update deletes the folder this came from.
        super().__init__((HOST, port), Handler)
        self.feed = feed
        bound = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
