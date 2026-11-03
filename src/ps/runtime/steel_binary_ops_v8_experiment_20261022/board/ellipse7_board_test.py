"""Compatibility launcher; the unified integrity-gated runner replaces the draft."""
from pathlib import Path
import runpy
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    runpy.run_path(str(root / "run_board_acceptance.py"), run_name="__main__")
