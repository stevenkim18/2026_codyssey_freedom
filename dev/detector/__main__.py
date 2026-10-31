"""Run with python -m detector; importing this module does not start a server."""
from .server import main


if __name__ == "__main__":
    raise SystemExit(main())
