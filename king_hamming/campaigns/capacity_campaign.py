#!/usr/bin/env python3
"""A separate continuous policy: existing single-host matching, minimal-owner fallback planning."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from campaigns.king_hamming import main

if __name__ == "__main__":
    raise SystemExit(main(policy_name="capacity"))
