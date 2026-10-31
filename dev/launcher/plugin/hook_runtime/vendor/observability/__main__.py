"""Run with ``python3 -m observability`` from ``dev``."""
import argparse
from pathlib import Path
import sys
import threading
import time

from observability.feed import Feed, default_journal
from observability.server import DEFAULT_PORT, DashboardServer, HOST


def _stop_when_removed(server):
    # Updating or removing the plugin deletes this copy; the next hook starts the new one.
    source = Path(__file__)
    while source.exists():
        time.sleep(2)
    server.shutdown()


def main():
    # The hooks start this under -I -S, where a C locale cannot encode Korean output.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="observability", description="Codex 차단 사건 로컬 대시보드")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--journal", type=Path, default=default_journal(), help="읽을 events.sqlite3 경로")
    options = parser.parse_args()
    try:
        server = DashboardServer(Feed(options.journal), options.port)
    except OSError:
        parser.error(f"{HOST}:{options.port} 포트를 열 수 없습니다. --port로 다른 포트를 지정하세요.")
    print(f"대시보드: http://{HOST}:{server.server_address[1]}  (종료: Ctrl+C)", flush=True)
    threading.Thread(target=_stop_when_removed, args=(server,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
