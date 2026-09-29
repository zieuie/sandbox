#!/usr/bin/env python3
"""Compatibility command; implementation lives in dp_solver/queue_tiles_smoke.py."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dp_solver.queue_tiles_smoke import main

if __name__ == "__main__":
    raise SystemExit(main())
