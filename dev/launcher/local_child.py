"""Fixed internal bootstrap: serve the parent's reserved loopback socket."""
import os
import socket
import ssl
import sys

import uvicorn


def main(argv=None):
    listener = None
    try:
        args = sys.argv[1:] if argv is None else argv
        if args not in (["detector"], ["gateway"]):
            return 2
        fd = int(os.environ["PII_LOCAL_LISTENER_FD"])
        if fd < 3:
            return 2
        listener = socket.socket(fileno=fd)
        tls = {}
        if args == ["detector"]:
            from detector.server import ServerSettings, create_server
            settings = ServerSettings.from_env(os.environ)
            if settings.host != "127.0.0.1" or settings.tls_cert_file is None:
                return 2
            tls = {"ssl_certfile":str(settings.tls_cert_file), "ssl_keyfile":str(settings.tls_key_file),
                   "ssl_version":ssl.PROTOCOL_TLS_SERVER, "ssl_keyfile_password":""}
        else:
            from gateway.settings import Settings
            from gateway.server import create_server
            settings = Settings.from_env(os.environ)
        if (listener.family != socket.AF_INET or listener.type != socket.SOCK_STREAM
                or listener.getsockname() != ("127.0.0.1", settings.port)):
            return 2
        # SO_ACCEPTCONN getsockopt is unavailable on some macOS Python builds.
        # Reassert listening on the same inherited bound descriptor portably.
        listener.listen(128)
        config = uvicorn.Config(create_server(settings), host="127.0.0.1", port=settings.port,
                                access_log=False, log_level="critical", **tls)
        uvicorn.Server(config).run(sockets=[listener])
        return 0
    except Exception:
        return 2
    finally:
        if listener is not None:
            listener.close()


if __name__ == "__main__":
    raise SystemExit(main())
