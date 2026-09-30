#!/usr/bin/env python3
"""Compatibility command for the relocated King Hamming campaign policy."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from campaigns.king_hamming import *  # noqa: F401,F403 - compatibility API
from campaigns.king_hamming import main


if __name__ == "__main__":
    raise SystemExit(main())
