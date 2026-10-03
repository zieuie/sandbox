#!/usr/bin/env python3
"""Measure block-mode matching on saved fields that also fit one GPU: residuals, rounds, time per P."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify  # noqa: E402

KERNEL = HERE / "kh_gpu_block_kernel"
RESULTS = ROOT / "cluster/deployments/continuous-campaign/results"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        epilog="Example: python3 tests/bench_blocks.py 2_23 --blocks 2,4,8 --verify")
    parser.add_argument("field", nargs="?", help="saved field such as 2_23")
    parser.add_argument("--blocks", default="2,4,8,16,32,64", help="comma-separated block counts; 0 lets the kernel size blocks from free device memory")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-bytes", type=int, default=2**31, help="host memory limit passed to the kernel")
    parser.add_argument("--max-rounds", type=int, default=16)
    parser.add_argument("--verify", action="store_true", help="run the Python KHM1 verifier on full matchings")
    parser.add_argument("--output", type=Path, default=HERE / "results/blocks.jsonl")
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    path = sorted(glob.glob(str(RESULTS / f"{arguments.field}_*.khdp")))[0]
    dp, digest = load_dp(path)
    n = request_count(dp)
    with tempfile.TemporaryDirectory(prefix="gpu-blocks-") as temporary:
        directory = Path(temporary)
        blocks = directory / "blocks.txt"
        blocks.write_text(f"{len(dp['runs'])}\n" + "".join(f"{r['a']} {r['t'] * r['repeat']}\n" for r in dp["runs"]))
        for count in (int(value) for value in arguments.blocks.split(",")):
            payload = directory / f"payload-{count}.bin"
            started = time.time()
            done = subprocess.run(
                [str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(payload), 
                 *(["--block-requests", str(-(-n // count))] if count else []), "--threads", str(arguments.threads), "--max-bytes", str(arguments.max_bytes),
                 "--max-rounds", str(arguments.max_rounds), "--max-residual", str(n)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            metadata = None
            for line in done.stdout.splitlines():
                record = json.loads(line)
                if "polynomial" in record and "status" in record:
                    metadata = record
            if metadata is None:
                print(f"{arguments.field} P={count}: failed {done.stderr[-300:]}")
                continue
            row = {"field": arguments.field, "requested_blocks": count, "blocks": metadata["blocks"],
                   "exit": done.returncode, "rounds": metadata["rounds"], "residual_round1": metadata["residual_round1"],
                   "residual": metadata["residual"], "round_log": [(r["imports"], r["matched"]) for r in metadata["round_log"]][:8],
                   "seconds": metadata["seconds"], "wall": round(time.time() - started, 1), "device": metadata["device"]}
            if done.returncode == 0 and arguments.verify:
                output = directory / f"result-{count}.khmatch"
                publish(output, header(dp, digest, metadata), payload)
                summary = verify(output, dp, digest)
                row["verified"] = bool(summary["verified"] and summary["status"] == "full_matching")
            print(json.dumps(row))
            arguments.output.parent.mkdir(exist_ok=True)
            with arguments.output.open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            payload.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
