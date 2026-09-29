#!/usr/bin/env python3
"""Exercise estimator bounds, tiled DP, restart, and artifact compatibility."""

from __future__ import annotations

import json
import os
import struct
import selectors
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


# Run a command and retain readable diagnostics on failure.
def run(arguments: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    """Run arguments and require the specified exit status."""

    result = subprocess.run(arguments, text=True, capture_output=True, timeout=60)

    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}: {arguments}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    return result


# Decode the existing KHD1 fixture through its independent inspector.
def fixture_document(name: str) -> dict[str, Any]:
    """Return readable JSON for one checked-in DP artifact."""

    result = run(
        [
            sys.executable,
            str(PROJECT / "scripts" / "inspect_artifact.py"),
            str(PROJECT / "examples" / name),
            "--verify",
        ]
    )
    document = json.loads(result.stdout)
    assert isinstance(document, dict)
    return document


# Expand either draft run encoding into an ordered triple list.
def draft_steps(document: dict[str, Any]) -> list[list[int]]:
    """Return expanded [a,b,t] records from a draft artifact."""

    output: list[list[int]] = []

    for run_record in document["runs"]:
        output.extend(
            [[run_record["a"], run_record["b"], run_record["t"]]] * run_record["repeat"]
        )

    return output


# Compare a fresh multi-tile result with one independently verified frozen fixture.
def check_case(
    temporary: Path,
    p: int,
    r: int,
    tile_side: int,
    fixture: str,
    force_resume: bool,
) -> None:
    """Run and verify one mathematical compatibility case."""

    work = temporary / f"work-{p}-{r}"
    artifact = temporary / f"split-{p}-{r}.json"
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
        "--checkpoint-seconds",
        "0",
    ]

    if force_resume:
        run(command + ["--stop-after-tiles", "2"], expected=75)

    run(command)
    run([sys.executable, str(ROOT / "verify_dp.py"), str(artifact)])
    draft = json.loads(artifact.read_text())
    fixture_document_data = fixture_document(fixture)
    assert draft["theta"] == fixture_document_data["theta"]
    assert draft_steps(draft) == fixture_document_data["split"]


# Compare complete state across schedules and replay partially persisted cells.
def check_threaded(temporary: Path) -> None:
    """Require byte-identical arrays and artifacts for all tested schedules."""

    allowed = sorted(os.sched_getaffinity(0))
    counts = sorted({1, min(2, len(allowed)), min(4, len(allowed))})
    reference: dict[str, bytes] | None = None

    # Include narrow and clipped tiles, where many workers have empty ranges.
    for tile_side in (1, 4, 7, 64):
        for threads in counts:
            work = temporary / f"threaded-{tile_side}-{threads}"
            artifact = temporary / f"threaded-{tile_side}-{threads}.json"
            command = [
                str(ROOT / "kh_dp_local"), "5", "3",
                "--work-dir", str(work), "-o", str(artifact),
                "--tile-side", str(tile_side), "--threads", str(threads),
            ]
            run(command)
            observed = {
                "values": (work / "values.bin").read_bytes(),
                "choices": (work / "choices.bin").read_bytes(),
                "artifact": artifact.read_bytes(),
            }

            if reference is None:
                reference = observed
            else:
                assert observed == reference, (tile_side, threads)

    # Simulate interrupted writes beyond a committed two-tile prefix.
    work = temporary / "poisoned-replay"
    artifact = temporary / "poisoned-replay.json"
    threads = counts[-1]
    command = [
        str(ROOT / "kh_dp_local"), "5", "3",
        "--work-dir", str(work), "-o", str(artifact),
        "--tile-side", "7", "--threads", str(threads),
    ]
    run(command + ["--stop-after-tiles", "2"], expected=75)

    # Tile (0,2) is not committed; its nonzero junk must never participate in replay.
    with (work / "values.bin").open("r+b") as values, (work / "choices.bin").open("r+b") as choices:
        for u in range(1, 8):
            for v in range(15, 22):
                index = u * 26 + v
                values.seek(index * 8)
                values.write(struct.pack("=Q", 0xFFFFFFFFFFFFFFFF))
                choices.seek(index * 4)
                choices.write(struct.pack("=I", 0xFFFFFFFF))

    # Resume with one worker; topology and thread count are not checkpoint identity.
    command[-1] = "1"
    run(command)
    assert reference is not None
    assert (work / "values.bin").read_bytes() == reference["values"]
    assert (work / "choices.bin").read_bytes() == reference["choices"]
    assert artifact.read_bytes() == reference["artifact"]
    run([sys.executable, str(ROOT / "verify_dp.py"), str(artifact)])

    # Deliver a real termination signal while a larger tile is being evaluated.
    signal_work = temporary / "signal-stop"
    signal_artifact = temporary / "signal-stop.json"
    signal_command = [
        str(ROOT / "kh_dp_local"), "7", "5",
        "--work-dir", str(signal_work), "-o", str(signal_artifact),
        "--tile-side", "343", "--threads", str(threads),
    ]
    process = subprocess.Popen(signal_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    try:
        assert process.stderr is not None

        # Startup acknowledgment is emitted only after every worker has pinned itself.
        with selectors.DefaultSelector() as selector:
            selector.register(process.stderr, selectors.EVENT_READ)
            assert selector.select(timeout=10), "workers did not start"
            placement = process.stderr.readline()

        assert placement.startswith(f"DP workers={threads}; CPUs:")
        assigned = [int(cpu) for cpu in placement.split("CPUs:")[1].split()]
        assert len(set(assigned)) == threads
        assert set(assigned).issubset(allowed)
        tasks = list(Path(f"/proc/{process.pid}/task").iterdir())
        pinned = [os.sched_getaffinity(int(task.name)) for task in tasks]

        # Multiworker runs leave the main dispatcher separate from pinned workers.
        for cpu in assigned:
            assert {cpu} in pinned

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 75, (stdout, stderr)
        assert not signal_artifact.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    run(signal_command)
    result = json.loads(signal_artifact.read_text())
    fixture = fixture_document("7_5.khdp")
    assert result["theta"] == fixture["theta"]
    assert draft_steps(result) == fixture["split"]

    # A restricted inherited CPU set must constrain every worker allocation.
    def restrict_cpu() -> None:
        """Restrict the test child to one available logical CPU."""

        os.sched_setaffinity(0, {allowed[0]})

    restricted = subprocess.run(
        [str(ROOT / "kh_dp_local"), "3", "3",
         "--work-dir", str(temporary / "restricted"),
         "-o", str(temporary / "restricted.json"), "--threads", "2"],
        preexec_fn=restrict_cpu,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert restricted.returncode == 1
    assert "allowed logical CPU" in restricted.stderr


# Independently count each cost group and its maximum-gain tie winners.
def check_transition_profiles() -> None:
    """Compare C reduction counts with independent Python residue sets."""

    for p in (2, 3, 5, 7, 11, 17, 31):
        groups: dict[tuple[int, int], list[int]] = {}

        for a in range(1, p + 1):
            for b in range(1, p + 1):
                for t in range(1, p + 1):
                    gain = t * len({(h * t - g) % p for g in range(a) for h in range(b)})
                    groups.setdefault((a * t, b * t), []).append(gain)

        profile = json.loads(run([
            str(ROOT / "kh_estimate"), str(p), "3", "--json", "--profile-transitions",
        ]).stdout)["transition_profile"]
        equal = sum(gains.count(max(gains)) - 1 for gains in groups.values())
        lower = sum(sum(gain < max(gains) for gain in gains) for gains in groups.values())
        assert profile["raw_count"] == p**3
        assert profile["cost_groups"] == len(groups)
        assert profile["equal_gain_removed"] == equal
        assert profile["lower_gain_removed"] == lower
        assert len(groups) + equal + lower == p**3
        assert profile["scan_bytes"] == len(groups) * 12
        assert profile["array_peak_bytes"] == p**3 * 12
        assert profile["array_bytes"] >= profile["scan_bytes"]
        assert profile["scan_visits"] == p**4 * len(groups)

    # Profiling is explicit and its admission cap is checked before allocation.
    run([str(ROOT / "kh_estimate"), "47", "3", "--profile-transitions"], expected=1)
    run([str(ROOT / "kh_estimate"), "11", "3", "--profile-transitions",
         "--max-profile-transitions", "1330"], expected=1)
    run([str(ROOT / "kh_estimate"), "11", "3", "--profile-transitions",
         "--max-profile-transitions", "1331"])


# Check pruning against raw scans, including checkpoints in either direction.
def check_reduction(temporary: Path) -> None:
    """Require byte-identical complete tables, choices, artifacts, and mode-switch resumes."""

    threads = min(4, len(os.sched_getaffinity(0)))

    for p, r in ((2, 3), (3, 5), (5, 3), (7, 3), (7, 5), (11, 3), (17, 3)):
        reference: tuple[bytes, bytes, bytes] | None = None

        # Compare a raw single tile with narrow threaded tiles and both resume directions.
        narrow_tile = min(7 if r == 3 else 64, p**((r + 1) // 2) - 1)
        schedules = (
            (True, 4096, 1, None),
            (False, narrow_tile, threads, None),
            (False, narrow_tile, 1, True),
            (True, narrow_tile, threads, False),
        )

        for index, (raw, tile, workers, stopped_raw) in enumerate(schedules):
            work = temporary / f"reduce-{p}-{r}-{index}"
            artifact = temporary / f"reduce-{p}-{r}-{index}.json"
            command = [str(ROOT / "kh_dp_local"), str(p), str(r),
                       "--work-dir", str(work), "-o", str(artifact),
                       "--tile-side", str(tile), "--threads", str(workers)]

            if stopped_raw is not None:
                first = command + ["--stop-after-tiles", "2"]

                if stopped_raw:
                    first += ["--raw-transitions"]

                run(first, expected=75)

            if raw:
                command += ["--raw-transitions"]

            completed = run(command)
            observed = ((work / "values.bin").read_bytes(),
                        (work / "choices.bin").read_bytes(), artifact.read_bytes())
            metrics = json.loads(next(line.removeprefix("DP metrics=")
                                      for line in completed.stderr.splitlines()
                                      if line.startswith("DP metrics=")))
            assert metrics["raw_transitions"] == p**3
            assert metrics["scan_transitions"] <= p**3
            assert metrics["mode"] == ("raw" if raw else "same-cost")

            if reference is None:
                reference = observed
                # Large cases use the complete unpruned table as their reference.
                if p**(r + 4) <= 200_000_000:
                    run([sys.executable, str(ROOT / "verify_dp.py"), str(artifact)])
            else:
                assert observed == reference, (p, r, index)


# Observe live cell progress during one uncommitted tile, then stop and resume it.
def check_live_progress(temporary: Path) -> None:
    """Require non-durable cell progress before a signal-boundary checkpoint."""

    artifact = temporary / "live-progress.json"
    command = [
        str(ROOT / "kh_dp_local"), "11", "5", "--threads", "1",
        "--tile-side", "4096", "--progress-milliseconds", "50",
        "--work-dir", str(temporary / "live-progress"), "-o", str(artifact),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    records: list[dict[str, Any]] = []
    pending = bytearray()

    try:
        assert process.stdout is not None

        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)

            # Wait for real work inside the first tile, rather than a tile commit.
            while not any(0 < row["done"] < row["total"] for row in records):
                assert selector.select(timeout=10), "no live solver heartbeat"
                block = os.read(process.stdout.fileno(), 65536)
                assert block, "solver completed before live progress was observed"
                pending.extend(block)

                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    records.append(json.loads(line))

        assert all(row["checkpoint_done"] == 0 for row in records)
        assert all(row["units"] == "cells" and row["heartbeat"] for row in records)
        process.send_signal(signal.SIGTERM)
        rest, stderr = process.communicate(timeout=30)
        records.extend(json.loads(line) for line in (bytes(pending) + rest).splitlines())
        assert process.returncode == 75, stderr
        assert [row["done"] for row in records] == sorted(row["done"] for row in records)
        assert records[-1]["checkpoint_done"] == records[-1]["total"]
        assert records[-1]["phase"] == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)

    run(command)
    assert json.loads(artifact.read_text())["theta"] == 4181


# Run all deterministic solver checks.
def main() -> int:
    """Execute the solver test suite and return an exit status."""

    # Heartbeats continue while a deliberately slow cell has made no progress.
    heartbeat = run([str(ROOT / "build" / "progress_unit"), "--run"])
    records = [json.loads(line) for line in heartbeat.stdout.splitlines()]
    assert sum(row["done"] == 0 for row in records) >= 2
    assert records[-1]["done"] == records[-1]["checkpoint_done"] == 1

    # The standard tile has the documented 192 MiB logical payload.
    estimate = run([str(ROOT / "kh_estimate"), "5", "3", "--json"])
    estimate_document = json.loads(estimate.stdout)
    assert estimate_document["tile_payload_bytes"] == 201_326_592
    assert estimate_document["state_bytes"] == 8_112
    assert estimate_document["transitions"] == 125

    # Large representable q reports saturated work without wrapping resource sizes.
    large = run([str(ROOT / "kh_estimate"), "1621", "3", "--json"])
    large_document = json.loads(large.stdout)
    assert large_document["q"] <= 0xFFFFFFFF
    assert large_document["estimated_visits_overflow"] is True

    # Composite p and overflowing q are rejected clearly.
    run([str(ROOT / "kh_estimate"), "4", "3"], expected=1)
    run([str(ROOT / "kh_estimate"), "1627", "3"], expected=1)

    run([str(ROOT / "build" / "transitions_unit"), "--run"])
    check_transition_profiles()

    with tempfile.TemporaryDirectory(prefix="kh-solver-test-") as temporary_name:
        temporary = Path(temporary_name)
        check_case(temporary, 3, 3, 4, "3_3.khdp", True)
        check_case(temporary, 5, 3, 7, "5_3.khdp", False)
        check_reduction(temporary)
        check_threaded(temporary)
        check_live_progress(temporary)

        # Existing artifacts remain immutable across accidental repeat commands.
        artifact = temporary / "split-5-3.json"
        work = temporary / "work-5-3"
        run(
            [
                str(ROOT / "kh_dp_local"),
                "5",
                "3",
                "--work-dir",
                str(work),
                "-o",
                str(artifact),
                "--tile-side",
                "7",
            ],
            expected=1,
        )

    print("solver checks passed")
    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
