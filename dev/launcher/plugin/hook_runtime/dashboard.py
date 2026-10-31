"""Background entry point the hooks start, using the plugin's own dashboard copy."""
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parent / "vendor")]

from observability.__main__ import main  # noqa: E402

main()
