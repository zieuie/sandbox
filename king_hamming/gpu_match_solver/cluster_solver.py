#!/usr/bin/env python3
"""Cluster bridge for match_gpu: lock the leased GPU, run the native kernel, publish KHM1."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "cluster"))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify  # noqa: E402
import gpus  # noqa: E402

KERNEL = HERE / "kh_gpu_match_kernel"


def emit(record: dict) -> None:
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def run(arguments: argparse.Namespace) -> int:
    dp, digest = load_dp(arguments.dp)
    total = request_count(dp)
    polynomial = [int(value) for value in arguments.poly.split(",")]

    # A local restart may find a fully published certificate; the agent validates it again.
    if arguments.output.exists():
        summary = verify(arguments.output, dp, digest, arguments.max_bytes)
        if summary["polynomial"] != polynomial:
            raise ValueError("existing certificate uses a different pinned field")
        emit({"done": summary["matched"], "total": total, "checkpoint_done": 0,
              "phase": "complete", "units": "requests"})
        return 0

    blocks = arguments.output.with_name("blocks.txt")
    raw = arguments.output.with_name("native-choices.bin")
    raw.unlink(missing_ok=True)
    with blocks.open("w") as stream:
        stream.write(f"{len(dp['runs'])}\n")
        for entry in dp["runs"]:
            stream.write(f"{entry['a']} {entry['t'] * entry['repeat']}\n")

    device = int(os.environ.get("KH_GPU_DEVICE", "0"))
    lock = gpus.DeviceLock(device)
    emit({"done": 0, "total": total, "checkpoint_done": 0, "phase": "waiting for gpu",
          "units": "requests", "heartbeat": True})
    # Opportunistic DP tiles hold the device for seconds at a time; wait for them.
    if not lock.acquire(timeout=arguments.lock_seconds):
        raise RuntimeError(f"GPU {device} stayed busy for {arguments.lock_seconds} s")
    metadata = None
    try:
        command = [str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(raw),
                   "--poly", arguments.poly, "--threads", str(arguments.threads),
                   "--max-bytes", str(arguments.max_bytes), "--device", str(device)]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=sys.stderr, text=True) as child:
            assert child.stdout is not None
            for line in child.stdout:
                record = json.loads(line)
                if "polynomial" in record and "status" in record:
                    metadata = record
                elif record.get("event") == "resource_usage" or "done" in record:
                    emit(record)
            code = child.wait()
    finally:
        lock.release()
    if code not in (0, 2) or metadata is None or metadata["status"] != (code == 2):
        raise RuntimeError(f"GPU matching kernel failed: exit={code}")
    if metadata["polynomial"] != polynomial:
        raise RuntimeError("GPU kernel used a different polynomial")

    # The agent independently verifies the KHM1 (validate_result) before publication.
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(10):
            emit({"done": metadata["matched"], "total": total, "checkpoint_done": 0,
                  "phase": "publishing", "units": "requests", "heartbeat": True})

    reporter = threading.Thread(target=heartbeat, daemon=True)
    reporter.start()
    try:
        publish(arguments.output, header(dp, digest, metadata), raw)
    finally:
        stop.set()
    raw.unlink(missing_ok=True)
    summary = {key: metadata[key] for key in ("matched", "required", "phases", "scans", "engine", "device")}
    summary["seconds"] = metadata.get("seconds")
    emit({"done": metadata["matched"], "total": metadata["required"], "checkpoint_done": 0,
          "phase": "complete", "units": "requests",
          "message": json.dumps(summary, separators=(",", ":"))})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 cluster_solver.py --dp input.khdp --output result.khmatch "
        "--poly 2,3,0,1 --threads 4 --max-bytes 4294967296"))
    parser.add_argument("--dp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poly", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--lock-seconds", type=float, default=1800)
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    if arguments.threads < 1 or arguments.max_bytes < 1:
        parser.error("invalid resource controls")
    return run(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"gpu_match_solver/cluster_solver.py: {error}", file=sys.stderr)
        raise SystemExit(1)
