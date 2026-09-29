#!/usr/bin/env python3
"""Apply a Linux CPU set and replace this process with a solver."""

from __future__ import annotations

import argparse
import ctypes
import signal
import os
import sys


# Parse the normalized comma-separated list produced by the node agent.
def parse_cpus(value: str) -> set[int]:
    """Return a validated set of logical CPU identifiers."""

    try:
        cpus = {int(item) for item in value.split(",") if item}
    except ValueError as error:
        raise argparse.ArgumentTypeError("CPUs must be comma-separated integers") from error

    if not cpus:
        raise argparse.ArgumentTypeError("at least one CPU is required")

    return cpus


# Build the internal affinity-wrapper interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the affinity-wrapper parser."""

    parser = argparse.ArgumentParser(
        description="Pin this process and exec a solver command.",
        epilog="Example: ./affinity_exec.py --cpus 0,2 -- ../dp_solver/kh_estimate 5 3",
    )
    parser.add_argument("--parent-pid", type=int, help="terminate with this supervising agent on Linux")
    parser.add_argument("--cpus", required=True, type=parse_cpus)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


# Apply affinity before any solver threads or allocations exist.
def main() -> int:
    """Set process affinity and replace this process with the requested command."""

    parser = build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    arguments = parser.parse_args()
    command = arguments.command

    if command and command[0] == "--":
        command = command[1:]

    if not command:
        parser.error("a command is required after --")

    # Kill an orphan even if the agent disappears during a long computation tile.
    if arguments.parent_pid is not None:
        libc = ctypes.CDLL(None, use_errno=True)

        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot set parent-death signal")

        if os.getppid() != arguments.parent_pid:
            return 75

    os.sched_setaffinity(0, arguments.cpus)
    os.execvp(command[0], command)
    return 127


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
