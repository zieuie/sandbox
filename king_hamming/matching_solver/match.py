#!/usr/bin/env python3
"""Run the exact C matcher from an immutable DP artifact and retain verified KHM1 certificates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matching_solver.artifacts import header, load_dp, publish, retain, verify


# A checkpointed pause is intentional and distinct from an operational failure.
class Paused(Exception):
    """Signal that the native solver safely stopped after saving a committed phase."""


# Separate one local field attempt from retry policy and cluster orchestration.
def attempt(dp, dp_digest, blocks, directory, arguments, start):
    """Run one native attempt for validated dp/blocks in directory using arguments/start; return metadata,payload."""
    payload = directory / "choices.bin"
    command = [str(Path(__file__).resolve().parent / "kh_match_kernel"), str(dp["p"]), str(dp["r"]),
               str(blocks), str(payload), "--threads", str(arguments.threads),
               "--max-bytes", str(arguments.max_bytes)]
    if arguments.poly is not None:
        command.extend(["--poly", arguments.poly])
    else:
        command.extend(["--start", str(start)])
    if arguments.checkpoint is not None:
        command.extend(["--checkpoint", str(arguments.checkpoint),
                        "--dp-hash", dp_digest.hex(),
                        "--checkpoint-seconds", str(arguments.checkpoint_seconds)])
    if arguments.resume is not None:
        command.extend(["--resume", str(arguments.resume)])
    if arguments.stop_after_phases:
        command.extend(["--stop-after-phases", str(arguments.stop_after_phases)])
    completed = subprocess.run(command, stdout=subprocess.PIPE, text=True)
    if completed.returncode == 3:
        raise Paused(f"saved {arguments.checkpoint}; resume with --resume {arguments.checkpoint}")
    if completed.returncode not in (0, 2):
        raise RuntimeError(f"matching kernel failed with exit status {completed.returncode}")
    records = [json.loads(line) for line in completed.stdout.splitlines() if line]
    metadata = next((record for record in records
                     if "polynomial" in record and "status" in record), None)
    if metadata is None:
        raise ValueError("matching kernel omitted result metadata")
    if metadata["status"] != (completed.returncode == 2):
        raise ValueError("kernel exit status disagrees with result")
    return metadata, payload


# Expose a small useful DP-to-certificate command without involving the running cluster.
def main():
    """Parse CLI arguments, run verified attempts, and return zero for success/help or two for pinned obstruction."""
    parser = argparse.ArgumentParser(description="Find any full matching from a saved KHD1 DP split using the C kernel.",
        epilog="Example: python3 match.py ../examples/5_3.khdp -o /tmp/match_5_3.khmatch")
    parser.add_argument("dp", type=Path, nargs="?")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--poly", help="pin a primitive polynomial, low-degree-first, e.g. 2,3,0,1")
    parser.add_argument("--start", type=int, default=1, help="first packed polynomial candidate in automatic mode")
    parser.add_argument("--max-attempts", type=int, default=0, help="optional automatic attempt limit; zero tries until candidate exhaustion")
    parser.add_argument("--threads", type=int, default=1, help="pinned field-building and matching workers")
    parser.add_argument("--checkpoint", type=Path, help="replaceable phase-boundary checkpoint path; requires --poly")
    parser.add_argument("--resume", type=Path, help="restore a verified checkpoint; requires the same --poly and DP")
    parser.add_argument("--checkpoint-seconds", type=int, default=1800, help="minimum seconds between phase checkpoints; zero saves every phase")
    parser.add_argument("--stop-after-phases", type=int, default=0, help="pause safely after this completed phase; zero disables")
    parser.add_argument("--max-bytes", type=int, default=2**31, help="native address-space limit and independent verifier memory admission")
    arguments = parser.parse_args()
    if arguments.dp is None:
        parser.print_help()
        return 0
    if arguments.output is None:
        parser.error("-o/--output is required")
    if arguments.threads < 1 or arguments.max_bytes < 1 or not 0 <= arguments.start <= 2**32 - 1 or arguments.max_attempts < 0 or arguments.checkpoint_seconds < 0 or arguments.stop_after_phases < 0:
        parser.error("invalid resource controls or polynomial attempt range")
    if arguments.resume is not None and arguments.checkpoint is None:
        arguments.checkpoint = arguments.resume
    if (arguments.checkpoint is not None or arguments.resume is not None) and arguments.poly is None:
        parser.error("checkpoint and resume currently require --poly")
    if arguments.stop_after_phases and arguments.checkpoint is None:
        parser.error("--stop-after-phases requires --checkpoint")
    if os.path.lexists(arguments.output):
        raise FileExistsError(f"output already exists: {arguments.output}")
    dp, dp_digest = load_dp(arguments.dp)
    start = arguments.start
    if arguments.poly is None and start >= dp["q"]:
        raise ValueError("starting polynomial candidate exceeds the field candidate range")
    limit = 1 if arguments.poly is not None else (arguments.max_attempts or dp["q"] - start)
    with tempfile.TemporaryDirectory(prefix=".kh-match-", dir=arguments.output.parent) as temporary:
        directory = Path(temporary)
        blocks = directory / "blocks.txt"
        with blocks.open("w") as stream:
            stream.write(f"{len(dp['runs'])}\n")
            for run in dp["runs"]:
                stream.write(f"{run['a']} {run['t'] * run['repeat']}\n")
        for number in range(limit):
            workspace = directory / str(number)
            workspace.mkdir()
            metadata, payload = attempt(dp, dp_digest, blocks, workspace, arguments, start)
            candidate = workspace / "verified.khmatch"
            publish(candidate, header(dp, dp_digest, metadata), payload)
            summary = verify(candidate, dp, dp_digest, arguments.max_bytes)
            summary.update(phases=metadata["phases"], scans=metadata["scans"],
                           memory_required=metadata["memory_required"])
            if metadata["status"] == 0 or arguments.poly is not None:
                retain(candidate, arguments.output)
                summary["file"] = str(arguments.output)
                print(json.dumps(summary, indent=2))
                return 0 if metadata["status"] == 0 else 2
            failure = arguments.output.with_name(arguments.output.name + f".poly-{metadata['candidate']}.hall.khmatch")
            retain(candidate, failure)
            summary["file"] = str(failure)
            print(json.dumps(summary), file=sys.stderr)
            start = metadata["candidate"] + 1
            if start >= dp["q"]:
                raise RuntimeError("primitive polynomial candidates exhausted; certified failures retained")
    raise RuntimeError("automatic attempt limit reached; certified failures retained; resume with --start " + str(start))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Paused as error:
        print(f"match.py: checkpointed pause: {error}", file=sys.stderr)
        raise SystemExit(3)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"match.py: {error}", file=sys.stderr)
        raise SystemExit(1)
