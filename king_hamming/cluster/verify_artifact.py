#!/usr/bin/env python3
"""Verify artifacts through their registered solver adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import adapters


# Construct a standalone verifier interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the artifact-verifier parser."""

    parser = argparse.ArgumentParser(
        description="Verify an artifact through its adapter without invoking its solver.",
        epilog="Example: ./verify_artifact.py ../dp_solver/dp_5_3.json result.bin --sha256 EXPECTED_HASH",
    )
    parser.add_argument("specification", type=Path)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--sha256", help="require this content digest")
    parser.add_argument("--max-visits", type=int, default=200_000_000)
    return parser


# Hash and verify one supported artifact.
def main() -> int:
    """Verify the requested artifact and return an exit status."""

    parser = build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    arguments = parser.parse_args()
    specification = json.loads(arguments.specification.read_text())
    digest = hashlib.sha256(arguments.artifact.read_bytes()).hexdigest()

    if arguments.sha256 is not None and digest != arguments.sha256:
        parser.error("artifact SHA-256 does not match")

    try:
        adapters.get(specification).verify_result(specification, arguments.artifact, arguments.max_visits)
    except ValueError as error:
        parser.error(str(error))

    print(f"verified {arguments.artifact} sha256={digest}")
    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
