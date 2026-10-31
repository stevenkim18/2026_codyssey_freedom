"""Absolute import entry point for the standalone frozen application."""
from launcher.standalone import main


if __name__ == "__main__":
    raise SystemExit(main())
