#!/usr/bin/env python3
"""Build (and optionally enqueue) a pinned match_gpu_wide specification from a saved KHD1."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cluster"))
from dp_solver.artifacts import decode_dp  # noqa: E402
from gpu_wide_match_solver.adapter import MIN_DEVICE_BYTES, host_bytes  # noqa: E402


def specification(dp_path: Path, polynomial: str, threads: int = 4, max_bytes: int | None = None,
                  gpu_memory_bytes: int | None = None, block_device_bytes: int | None = None,
                  row_bytes: int | None = None) -> dict:
    """Return a canonical match_gpu_wide job for exact DP bytes and one pinned polynomial.

    gpu_memory_bytes is the device the lease must provide; the kernel sizes its blocks from it.
    max_bytes is the host memory the lease reserves; the kernel sizes its row passes from it.
    """
    raw = Path(dp_path).read_bytes()
    dp = decode_dp(raw)
    device = int(gpu_memory_bytes if gpu_memory_bytes is not None else MIN_DEVICE_BYTES)
    job = {"program": "match_gpu_wide", "arguments": {
        "dp_b64": base64.b64encode(raw).decode("ascii"),
        "dp_sha256": hashlib.sha256(raw).hexdigest(),
        "poly": [int(value) for value in polynomial.split(",")],
        "threads": int(threads),
        "max_bytes": int(max_bytes if max_bytes is not None else host_bytes(dp, threads, device)),
        "gpu_memory_bytes": device,
    }}
    if block_device_bytes is not None:
        job["arguments"]["block_device_bytes"] = int(block_device_bytes)
    if row_bytes is not None:
        job["arguments"]["row_bytes"] = int(row_bytes)
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 gpu_wide_match_solver/submit.py examples/7_5.khdp --poly 4,1,0,0,0,1 "
        "--leader http://127.0.0.1:8061 --enqueue"))
    parser.add_argument("dp", type=Path, nargs="?")
    parser.add_argument("--poly", required=False)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--gpu-memory-bytes", type=int)
    parser.add_argument("--block-device-bytes", type=int)
    parser.add_argument("--row-bytes", type=int)
    parser.add_argument("--leader")
    parser.add_argument("--priority", type=int, default=100)
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    arguments = parser.parse_args()
    if arguments.dp is None or arguments.poly is None:
        parser.print_help()
        return 0
    job = specification(arguments.dp, arguments.poly, arguments.threads, arguments.max_bytes,
                        arguments.gpu_memory_bytes, arguments.block_device_bytes, arguments.row_bytes)
    if not arguments.enqueue:
        print(json.dumps(job, indent=2))
        return 0
    if not arguments.leader:
        parser.error("--enqueue requires --leader")
    from dp_solver.launch_dp import request
    payload = {"specification": job, "priority": arguments.priority}
    if arguments.rerun:
        payload["rerun"] = True
    print(json.dumps(request(arguments.leader, "/v1/enqueue", payload)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
