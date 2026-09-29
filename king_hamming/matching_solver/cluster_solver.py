#!/usr/bin/env python3
"""Bridge the native matching checkpoint handshake to the generic cluster agent."""

from __future__ import annotations

import argparse
import json
import os
import threading
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matching_solver.artifacts import header, load_dp, publish, request_count, verify


# Expand only compact request blocks, never the full graph.
def write_blocks(dp: dict, path: Path) -> None:
    """Write native blocks for validated DP document dp to path; return no value."""
    with path.open("w") as stream:
        stream.write(f"{len(dp['runs'])}\n")
        for run in dp["runs"]:
            stream.write(f"{run['a']} {run['t'] * run['repeat']}\n")


# Read actual kernel CPU use so a long active phase has an honest heartbeat.
def cpu_ticks(pid: int) -> int | None:
    """Return process user+system CPU ticks, or None after the process exits."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text.rsplit(") ", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return None


# Relay phase progress and wait for the agent's durable snapshot acknowledgment.
def run(arguments: argparse.Namespace) -> int:
    """Execute one pinned field attempt, publish verified KHM1, and return process status."""
    dp, digest = load_dp(arguments.dp)
    blocks = arguments.output.with_name("blocks.txt")
    raw = arguments.output.with_name("native-choices.bin")

    # A local supervisor restart may find a fully published certificate already present.
    if arguments.output.exists():
        summary = verify(arguments.output, dp, digest, arguments.max_bytes)
        if summary["polynomial"] != [int(value) for value in arguments.poly.split(",")]:
            raise ValueError("existing certificate uses a different pinned field")
        print(json.dumps({"done": summary["matched"], "total": summary["required"],
                          "checkpoint_done": 0, "phase": "complete", "units": "requests"}), flush=True)
        return 0

    # Raw kernel output is disposable scratch, unlike the retained KHM1 artifact.
    raw.unlink(missing_ok=True)
    write_blocks(dp, blocks)
    command = [str(Path(__file__).with_name("kh_match_kernel")), str(dp["p"]), str(dp["r"]),
               str(blocks), str(raw), "--poly", arguments.poly,
               "--threads", str(arguments.threads), "--max-bytes", str(arguments.max_bytes),
               "--checkpoint", str(arguments.checkpoint), "--dp-hash", digest.hex(),
               "--checkpoint-seconds", str(arguments.checkpoint_seconds)]
    if arguments.resume:
        command.extend(["--resume", str(arguments.checkpoint)])
    if arguments.checkpoint_handshake:
        command.append("--checkpoint-handshake")
    metadata = None
    latest_progress = None
    output_lock = threading.Lock()
    reporter_stop = threading.Event()

    def emit(record: dict) -> None:
        """Write one complete JSON report without interleaving reporter threads."""
        with output_lock:
            os.write(sys.stdout.fileno(),
                     (json.dumps(record, separators=(",", ":")) + "\n").encode())

    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=sys.stderr, text=True, bufsize=1) as child:
        assert child.stdin is not None and child.stdout is not None

        def report_cpu_activity() -> None:
            """Report only when the native kernel has consumed more CPU time."""
            previous = cpu_ticks(child.pid)
            while not reporter_stop.wait(10):
                current = cpu_ticks(child.pid)
                if current is None:
                    break
                if previous is not None and current > previous:
                    progress = latest_progress or {}
                    emit({"done": progress.get("done", 0),
                          "total": progress.get("total", request_count(dp)),
                          "checkpoint_done": progress.get("checkpoint_done", 0),
                          "phase": progress.get("phase", "matching"),
                          "units": "requests", "heartbeat": True,
                          "message": "kernel CPU active"})
                previous = current

        reporter = threading.Thread(target=report_cpu_activity, daemon=True)
        reporter.start()
        for line in child.stdout:
            record = json.loads(line)
            if record.get("event") == "checkpoint":
                emit(record)
                if sys.stdin.readline() != "\n":
                    child.terminate()
                    raise RuntimeError("cluster checkpoint acknowledgment missing")
                child.stdin.write("\n")
                child.stdin.flush()
                if latest_progress is not None:
                    durable = dict(latest_progress)
                    durable["checkpoint_done"] = durable["done"]
                    emit(durable)
            elif "polynomial" in record and "status" in record:
                metadata = record
            else:
                latest_progress = record
                emit(record)
        code = child.wait()
        reporter_stop.set()
        reporter.join(timeout=1)
    if code not in (0, 2) or metadata is None or metadata["status"] != (code == 2):
        raise RuntimeError(f"native matching attempt failed: exit={code}")
    publish(arguments.output, header(dp, digest, metadata), raw)
    summary = verify(arguments.output, dp, digest, arguments.max_bytes)
    print(json.dumps({"done": metadata["matched"], "total": metadata["required"],
                      "checkpoint_done": 0, "phase": "complete", "units": "requests",
                      "message": json.dumps(summary, separators=(",", ":"))}), flush=True)
    return 0


# A no-argument invocation must provide a usable command example.
def main() -> int:
    """Parse isolated worker arguments and return zero on a verified matching or Hall artifact."""
    parser = argparse.ArgumentParser(description=__doc__, epilog="Example: python3 cluster_solver.py --dp input.khdp --output result.khmatch --checkpoint phase.khcp --poly 2,3,0,1 --threads 2 --max-bytes 2147483648 --checkpoint-seconds 1800")
    parser.add_argument("--dp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--poly", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--checkpoint-seconds", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--checkpoint-handshake", action="store_true")
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    if arguments.threads < 1 or arguments.max_bytes < 1 or arguments.checkpoint_seconds < 0:
        parser.error("invalid resource controls")
    return run(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"cluster_solver.py: {error}", file=sys.stderr)
        raise SystemExit(1)
