#!/usr/bin/env python3
"""Create or enqueue one pinned matching attempt from a saved KHD1 DP artifact."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cluster"))
from matching_solver.adapter import MatchingAdapter


# Keep the DP artifact a separate saved result while transferring its small exact bytes.
def specification(dp_path: Path, polynomial: str, threads: int, max_bytes: int,
                  distributed: bool = False, max_edges: int = 2_000_000,
                  max_field_elements: int = 1_000_000, workers: int = 2) -> dict:
    """Return a validated pinned field job for dp_path and operational controls."""
    raw = dp_path.read_bytes()
    try:
        coefficients = [int(value) for value in polynomial.split(",")]
    except ValueError as error:
        raise ValueError("polynomial must be comma-separated integers") from error
    document = {"program": "match_distributed" if distributed else "match", "arguments": {
        "dp_b64": base64.b64encode(raw).decode("ascii"),
        "dp_sha256": hashlib.sha256(raw).hexdigest(),
        "poly": coefficients, "threads": threads, "max_bytes": max_bytes,
    }}
    if distributed:
        document["arguments"].update(max_edges=max_edges,
                                     max_field_elements=max_field_elements,
                                     workers=workers)
    MatchingAdapter().validate(document)
    return document


# Print a complete example for an empty invocation.
def main() -> int:
    """Print a job or submit it to leader, returning zero after success."""
    parser = argparse.ArgumentParser(description=__doc__, epilog="Example: python3 submit.py examples/13_5.khdp --poly 2,4,0,0,0,1 --leader http://127.0.0.1:8041 --enqueue")
    parser.add_argument("dp", type=Path, nargs="?")
    parser.add_argument("--poly", help="pinned primitive polynomial, low-degree-first")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-bytes", type=int, default=2**31)
    parser.add_argument("--distributed", action="store_true", help="reserve a multi-node matching group")
    parser.add_argument("--workers", type=int, default=2,
                        help="nodes to reserve for one distributed matching (2-8)")
    parser.add_argument("--max-edges", type=int, default=2_000_000)
    parser.add_argument("--max-field-elements", type=int, default=1_000_000)
    parser.add_argument("--leader", default="http://127.0.0.1:8041")
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--rerun", action="store_true", help="retain another attempt of the same field")
    parser.add_argument("--priority", type=int, default=0)
    arguments = parser.parse_args()
    if arguments.dp is None:
        parser.print_help()
        return 0
    if not arguments.poly:
        parser.error("--poly is required for a restorable cluster attempt")
    job = specification(arguments.dp, arguments.poly, arguments.threads, arguments.max_bytes,
                        arguments.distributed, arguments.max_edges,
                        arguments.max_field_elements, arguments.workers)
    if arguments.enqueue:
        body = json.dumps({"specification": job, "priority": arguments.priority, "rerun": arguments.rerun}).encode()
        request = Request(arguments.leader.rstrip("/") + "/v1/enqueue", data=body,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            print(json.dumps(json.load(response), indent=2))
    else:
        print(json.dumps(job, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"submit.py: {error}", file=sys.stderr)
        raise SystemExit(1)
