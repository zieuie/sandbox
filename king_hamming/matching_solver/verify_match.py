#!/usr/bin/env python3
"""Independently verify compact matchings and optionally hydrate them to tab-separated rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matching_solver.artifacts import load_dp, retain, verify


# Verification uses independent polynomial arithmetic and never invokes the C search engine.
def main():
    """Verify requested artifact and optional TSV output; return zero for success/help, raise for invalid input."""
    parser = argparse.ArgumentParser(description="Verify every matching edge, endpoint uniqueness, and any Hall obstruction.",
        epilog="Example: python3 verify_match.py /tmp/match_5_3.khmatch --dp ../examples/5_3.khdp --hydrate /tmp/matching.tsv")
    parser.add_argument("artifact", type=Path, nargs="?")
    parser.add_argument("--dp", type=Path, help="referenced KHD1 split")
    parser.add_argument("--max-bytes", type=int, default=2**31)
    parser.add_argument("--hydrate", type=Path, help="write left/coset/prefix/suffix/right TSV to a fresh file")
    arguments = parser.parse_args()
    if arguments.artifact is None:
        parser.print_help()
        return 0
    if arguments.dp is None:
        parser.error("--dp is required")
    dp, digest = load_dp(arguments.dp)
    if arguments.hydrate is None:
        summary = verify(arguments.artifact, dp, digest, arguments.max_bytes)
    else:
        with tempfile.TemporaryDirectory(prefix=".kh-hydrate-", dir=arguments.hydrate.parent) as temporary:
            output = Path(temporary) / "matching.tsv"
            with output.open("w") as stream:
                summary = verify(arguments.artifact, dp, digest, arguments.max_bytes, stream)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            retain(output, arguments.hydrate)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"verify_match.py: {error}", file=sys.stderr)
        raise SystemExit(1)
