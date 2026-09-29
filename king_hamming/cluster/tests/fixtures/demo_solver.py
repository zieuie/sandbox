#!/usr/bin/env python3
"""Provide a deterministic checkpointable stand-in for a future C solver."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

def sync_directory(path: Path) -> None:
    """Durably publish a fixture checkpoint rename without production imports."""

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# Construct a no-surprises demonstration solver interface.
def build_parser() -> argparse.ArgumentParser:
    """Build the demonstration solver argument parser."""

    parser = argparse.ArgumentParser(
        description="Run a checkpointable demonstration calculation.",
        epilog="Example: ./demo_solver.py --steps 10 --delay 0.1 --checkpoint state.json --output result.json",
    )
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint-handshake", action="store_true", help="pause for agent snapshot acknowledgment")
    parser.add_argument("--fail-once-at", type=int)
    return parser


# Publish checkpoint contents atomically.
def write_checkpoint(path: Path, next_step: int) -> None:
    """Record next_step through a temporary file and atomic rename."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w") as output:
        output.write(json.dumps({"next_step": next_step}) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


# Load a prior safe boundary when one exists.
def read_checkpoint(path: Path) -> int:
    """Return the next unfinished step from path, or one for a fresh run."""

    if not path.exists():
        return 1

    value = json.loads(path.read_text())
    return int(value["next_step"])


# Emit progress in the line protocol consumed by the node agent.
def main() -> int:
    """Run the demonstration calculation and return an exit status."""

    parser = build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    arguments = parser.parse_args()
    stop_requested = False

    # A signal stops at the next completed demonstration step.
    def request_stop(signal_number: int, frame: object) -> None:
        """Latch a request to stop without touching checkpoint files in a handler."""

        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    first_step = read_checkpoint(arguments.checkpoint)
    failure_marker = arguments.checkpoint.with_suffix(".failed-once")

    for step in range(first_step, arguments.steps + 1):
        time.sleep(arguments.delay)
        write_checkpoint(arguments.checkpoint, step + 1)
        print(json.dumps({"done": step, "total": arguments.steps, "message": "demo"}), flush=True)

        # Keep the atomic single-file checkpoint fixed while the agent captures it.
        if arguments.checkpoint_handshake:
            print(json.dumps({"event": "checkpoint", "cursor": step}), flush=True)

            if sys.stdin.buffer.read(1) != b"\n":
                print("checkpoint snapshot handshake failed", file=sys.stderr)
                return 1

        if stop_requested:
            return 75

        # Provide a deterministic way to test supervisor recovery once.
        if arguments.fail_once_at == step and not failure_marker.exists():
            failure_marker.write_text("failed\n")
            return 75

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps({"steps": arguments.steps}, sort_keys=True) + "\n")
    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
