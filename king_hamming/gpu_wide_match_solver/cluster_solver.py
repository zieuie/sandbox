#!/usr/bin/env python3
"""Cluster bridge for match_gpu_wide: lock the leased GPU, run the wide kernel, publish KHM1 in place.

As gpu_block_match_solver/cluster_solver.py, except that the payload is never copied: the kernel
writes it after a zeroed space the size of the KHM1 header (--payload-offset), and the bridge
writes the header there, appends the checksum and links the file to its final name. The agent's
blob store and the feeder's archive then hard-link the same file (a 7^13 certificate is 206 GB).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "cluster"))
from gpu_block_match_solver.cluster_solver import Live, STAGES, emit  # noqa: E402
from matching_solver.artifacts import header, load_dp, publish_in_place, request_count, verify  # noqa: E402
import gpus  # noqa: E402

KERNEL = HERE / "kh_gpu_wide_kernel"
STAGES.setdefault("check", "checking the result")


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
    work = arguments.output.with_name("certificate.partial")   # header space, then the payload
    work.unlink(missing_ok=True)
    with blocks.open("w") as stream:
        stream.write(f"{len(dp['runs'])}\n")
        for entry in dp["runs"]:
            stream.write(f"{entry['a']} {entry['t'] * entry['repeat']}\n")
    # The header of a full matching is known before the run; any other outcome publishes nothing.
    expected = {"p": dp["p"], "r": dp["r"], "required": total, "status": 0, "matched": total,
                "polynomial": polynomial}
    prefix = header(dp, digest, expected)

    device = int(os.environ.get("KH_GPU_DEVICE", "0"))
    lock = gpus.DeviceLock(device)
    live = Live(total)
    live.update("gpu_wait", 0, None)
    if not lock.acquire(timeout=arguments.lock_seconds, hold="long"):
        raise RuntimeError(f"GPU {device} stayed busy for {arguments.lock_seconds} s")
    metadata = None
    try:
        command = [str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(work),
                   "--poly", arguments.poly, "--threads", str(arguments.threads),
                   "--max-bytes", str(arguments.max_bytes), "--device", str(device),
                   "--payload-offset", str(len(prefix))]
        if arguments.block_device_bytes:
            command += ["--block-device-bytes", str(arguments.block_device_bytes)]
        if arguments.row_bytes:
            command += ["--row-bytes", str(arguments.row_bytes)]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=sys.stderr, text=True) as child:
            assert child.stdout is not None
            for line in child.stdout:
                record = json.loads(line)
                if "polynomial" in record and "status" in record:
                    metadata = record
                elif record.get("event") == "resource_usage":
                    emit(record)
                elif "stage" in record:
                    live.kernel_line(record)
                elif "done" in record:
                    emit(record)
            code = child.wait()
    finally:
        lock.release()
    if code == 4 and metadata is not None:
        raise RuntimeError(f"wide matching incomplete: {metadata['residual']} requests unmatched after "
                           f"{metadata['rounds']} rounds in {metadata['blocks']} blocks and "
                           f"{metadata['passes']} passes (not an obstruction)")
    if code != 0 or metadata is None or metadata["status"] != 0:
        raise RuntimeError(f"GPU wide matching kernel failed: exit={code}")
    if header(dp, digest, metadata) != prefix:
        raise RuntimeError("GPU kernel result disagrees with the reserved header")

    # The agent independently verifies the KHM1 (validate_result) before publication.
    live.matched = metadata["matched"]
    live.update("publish", 0, work.stat().st_size)
    publish_in_place(work, arguments.output, prefix,
                     progress=lambda hashed, size: live.update("publish", hashed, size))
    live.finish()
    summary = {key: metadata[key] for key in ("matched", "required", "phases", "scans", "engine", "device",
                                             "blocks", "passes", "rescue_passes", "rounds", "residual_round1",
                                             "imported")}
    summary["seconds"] = metadata.get("seconds")
    live.extra = summary
    live.trace = metadata.get("trace") or live.trace
    emit({"done": metadata["matched"], "total": metadata["required"], "checkpoint_done": 0,
          "phase": "complete", "units": "requests", "message": live.message()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 cluster_solver.py --dp input.khdp --output result.khmatch "
        "--poly 2,3,0,1 --threads 4 --max-bytes 4294967296"),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poly", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--block-device-bytes", type=int, default=0)
    parser.add_argument("--row-bytes", type=int, default=0)
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
        print(f"gpu_wide_match_solver/cluster_solver.py: {error}", file=sys.stderr)
        raise SystemExit(1)
