#!/usr/bin/env python3
"""Collect reproducible local timing baselines for the tiled DP solver."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


# Parse one PRIME:DEGREE benchmark case.
def parse_case(text: str) -> tuple[int, int]:
    """Return a positive (p,r) pair from PRIME:DEGREE text."""

    try:
        p_text, r_text = text.split(":", 1)
        p = int(p_text)
        r = int(r_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("case must be PRIME:DEGREE") from error

    if p <= 0 or r <= 0:
        raise argparse.ArgumentTypeError("case values must be positive")

    return p, r


# Construct a useful no-argument benchmark interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the benchmark argument parser."""

    parser = argparse.ArgumentParser(
        description="Benchmark feasible local tiled-DP cases and emit JSON Lines.",
        epilog="Example: ./benchmark.py --case 7:5 --tile-side 4096 --threads 4",
    )
    parser.add_argument("--case", action="append", type=parse_case, dest="cases")
    parser.add_argument("--verify-with-raw", action="store_true",
                        help="compare full state and artifacts against the alternate scan mode")
    parser.add_argument("--raw-transitions", action="store_true", help="unpruned reference scan")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--tile-side", type=int, default=4096)
    parser.add_argument("--max-state-bytes", type=int, default=17_179_869_184)
    parser.add_argument("--max-visits", type=int, default=5_000_000_000)
    parser.add_argument("--keep", type=Path, help="retain work and artifacts under this directory")
    return parser


# Run one command and return its standard output.
def checked_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run arguments and return captured output, raising on failure."""

    result = subprocess.run(arguments, text=True, capture_output=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(arguments)}\n{result.stderr}"
        )

    return result


# Decode command output while preserving the common checked-execution path.
def checked_output(arguments: list[str]) -> str:
    """Run arguments and return stdout, raising on command failure."""

    return checked_run(arguments).stdout


# Estimate, execute, and independently verify one benchmark case.
def benchmark_case(
    p: int,
    r: int,
    tile_side: int,
    threads: int,
    raw_transitions: bool,
    verify_with_raw: bool,
    max_state_bytes: int,
    max_visits: int,
    root: Path,
) -> dict[str, Any]:
    """Return measured and estimated data for one completed DP case."""

    estimate = json.loads(
        checked_output(
            [
                str(ROOT / "kh_estimate"),
                str(p),
                str(r),
                "--tile-side",
                str(tile_side),
                "--json",
            ]
        )
    )
    mode = "raw" if raw_transitions else "same-cost"
    work = root / f"work-{p}-{r}-{mode}"
    artifact = root / f"split-{p}-{r}-{mode}.json"
    command = [
        str(ROOT / "kh_dp_local"),
        str(p),
        str(r),
        "--work-dir",
        str(work),
        "-o",
        str(artifact),
        "--tile-side",
        str(tile_side),
        "--threads",
        str(threads),
        "--max-state-bytes",
        str(max_state_bytes),
        "--max-visits",
        str(max_visits),
    ]
    if raw_transitions:
        command.append("--raw-transitions")

    started = time.perf_counter()
    completed = checked_run(command)
    elapsed = time.perf_counter() - started
    if verify_with_raw:
        alternate_work = root / f"reference-{p}-{r}-{mode}"
        alternate_artifact = root / f"reference-{p}-{r}-{mode}.json"
        alternate = command.copy()
        alternate[alternate.index("--work-dir") + 1] = str(alternate_work)
        alternate[alternate.index("-o") + 1] = str(alternate_artifact)

        if raw_transitions:
            alternate.remove("--raw-transitions")
        else:
            alternate.append("--raw-transitions")

        checked_run(alternate)

        for name in ("values.bin", "choices.bin"):
            if not filecmp.cmp(work / name, alternate_work / name, shallow=False):
                raise RuntimeError(f"alternate scan mode produced different {name}")

        if not filecmp.cmp(artifact, alternate_artifact, shallow=False):
            raise RuntimeError("alternate scan mode produced a different artifact")
    else:
        checked_output([sys.executable, str(ROOT / "verify_dp.py"), str(artifact)])
    result = json.loads(artifact.read_text())
    metrics = json.loads(next(line.removeprefix("DP metrics=")
                              for line in completed.stderr.splitlines()
                              if line.startswith("DP metrics=")))
    return {
        **metrics,
        "verification": "complete alternate-mode comparison" if verify_with_raw else "independent Python DP",
        "p": p,
        "r": r,
        "theta": result["theta"],
        "elapsed_seconds": elapsed,
        "active_cells": estimate["budget"] ** 2,
        "transitions": estimate["transitions"],
        "estimated_visits": estimate["estimated_visits"],
        "state_bytes": estimate["state_bytes"],
        "tile_side": tile_side,
        "threads": threads,
        "cpu_placement": next(
            line for line in completed.stderr.splitlines() if line.startswith("DP workers=")
        ),
        "host": platform.node(),
        "allowed_cpus": sorted(os.sched_getaffinity(0)),
    }


# Execute requested cases without choosing an implicit potentially expensive set.
def main() -> int:
    """Run requested benchmarks and return an exit status."""

    parser = build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    arguments = parser.parse_args()

    if arguments.threads < 1:
        parser.error("--threads must be positive")

    if not arguments.cases:
        parser.error("at least one --case is required")

    if arguments.keep is not None:
        arguments.keep.mkdir(parents=True, exist_ok=True)
        roots = [arguments.keep]
        context = None
    else:
        context = tempfile.TemporaryDirectory(prefix="kh-benchmark-")
        roots = [Path(context.name)]

    try:
        for p, r in arguments.cases:
            result = benchmark_case(
                p,
                r,
                arguments.tile_side,
                arguments.threads,
                arguments.raw_transitions,
                arguments.verify_with_raw,
                arguments.max_state_bytes,
                arguments.max_visits,
                roots[0],
            )
            print(json.dumps(result, sort_keys=True), flush=True)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"benchmark.py: {error}", file=sys.stderr)
        return 1
    finally:
        if context is not None:
            context.cleanup()

    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
