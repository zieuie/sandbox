#!/usr/bin/env python3
"""Check the production C coordinator, C shards, checkpoints, and certificates."""

from __future__ import annotations

import subprocess
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(PROJECT))
from matching_solver.artifacts import load_dp, verify
from matching_solver import prototype_checkpoint


def launch(*arguments: object, expected: int = 0) -> subprocess.CompletedProcess:
    """Run the cluster-only bridge and require one exact exit status."""
    result = subprocess.run([sys.executable, str(ROOT / "native_coordinator.py"),
                             *map(str, arguments)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=30)
    if result.returncode != expected:
        raise AssertionError(f"native coordinator exit {result.returncode}:\n{result.stdout}\n{result.stderr}")
    return result


def main() -> int:
    """Exercise native solve plus shard-count-independent KHS1 resume."""
    with tempfile.TemporaryDirectory(prefix="kh-native-distributed-") as name:
        temporary = Path(name)
        source = PROJECT / "examples/5_3.khdp"
        phases = temporary / "phases"
        launch(source, "--poly", "2,3,0,1", "-o", temporary / "paused.khmatch",
               "--worker", "local", "--worker", "local",
               "--checkpoint-dir", phases, "--worker-threads", "1,1",
               "--phase-batch-roots", 20, "--checkpoint-seconds", 0, "--stop-after-phases", 2,
               "--max-edges", 1_000_000, expected=3)
        checkpoint = phases / "phase-00000000000000000002.khstate"
        dp, digest = load_dp(source)
        phase, matched, _ = prototype_checkpoint.load(checkpoint, dp, digest, [2, 3, 0, 1])
        assert phase == 2 and 0 < matched <= 40, "checkpoint should be inside the first augmentation phase"
        output = temporary / "resumed.khmatch"
        launch(source, "--poly", "2,3,0,1", "-o", output,
               "--worker", "local", "--worker", "local", "--worker", "local",
               "--worker-threads", "1,1,1", "--checkpoint-dir", temporary / "resumed-phases",
               "--resume", checkpoint,
               "--max-edges", 1_000_000)
        assert verify(output, dp, digest)["status"] == "full_matching"

        larger = PROJECT / "examples/7_5.khdp"
        larger_output = temporary / "7_5.khmatch"
        launch(larger, "--poly", "4,1,0,0,0,1", "-o", larger_output,
               "--worker", "local", "--worker", "local", "--threads-per-worker", 2,
               "--max-edges", 10_000_000)
        larger_dp, larger_digest = load_dp(larger)
        assert verify(larger_output, larger_dp, larger_digest)["status"] == "full_matching"
        # Exercise repeated dynamic chunks and uneven per-host CPU allocations.
        uneven = temporary / "uneven.khmatch"
        threads = min(4, len(os.sched_getaffinity(0)))
        launch(larger, "--poly", "4,1,0,0,0,1", "-o", uneven,
               "--worker", "local", "--worker", "local",
               "--worker-threads", f"1,{threads}", "--max-edges", 10_000_000)
        assert verify(uneven, larger_dp, larger_digest)["status"] == "full_matching"
    print("native distributed matching, portable resume, and KHM1 verification passed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Check native distributed matching.\nExample: python3 tests/check_native_distributed.py --run")
    else:
        raise SystemExit(main())
