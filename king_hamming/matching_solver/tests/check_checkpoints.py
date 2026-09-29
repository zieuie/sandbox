#!/usr/bin/env python3
"""Exercise durable matching pause/resume and reject damaged or mismatched snapshots."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(PROJECT))
from dp_solver.artifacts import encode_dp
from matching_solver.artifacts import load_dp, verify


# Run one matching CLI command with an exact expected exit code.
def run(*command, expected=0):
    """Return a completed command, rejecting unexpected exit status."""
    result = subprocess.run([str(value) for value in command], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError((command, result.returncode, result.stdout, result.stderr))
    return result


# Verify phase-boundary snapshots across worker counts, corruption, and input changes.
def check():
    """Run the checkpoint regression; return no value on success."""
    source = PROJECT / "examples" / "5_3.khdp"
    dp, digest = load_dp(source)
    workers = min(4, len(os.sched_getaffinity(0)))
    with tempfile.TemporaryDirectory(prefix="kh-checkpoint-check-") as temporary:
        directory = Path(temporary)
        checkpoint = directory / "phase.khcp"
        pending = directory / "pending.khmatch"
        base = [sys.executable, ROOT / "match.py", source, "--poly", "2,3,0,1"]
        paused = run(*base, "-o", pending, "--threads", workers, "--checkpoint", checkpoint,
                     "--checkpoint-seconds", 0, "--stop-after-phases", 1, expected=3)
        assert "checkpointed pause" in paused.stderr and checkpoint.exists() and not pending.exists()
        first = checkpoint.read_bytes()
        assert first[:4] == b"KHC1" and len(first) > 8 * dp["q"]
        resumed = directory / "resumed.khmatch"
        other_threads = 1 if workers > 1 else workers
        result = run(*base, "-o", resumed, "--threads", other_threads,
                     "--resume", checkpoint, "--checkpoint-seconds", 0)
        summary = json.loads(result.stdout)
        assert summary["verified"] and summary["matched"] == dp["q"]
        assert verify(resumed, dp, digest)["verified"]
        assert checkpoint.read_bytes() != first, "later committed phases must advance the cursor"
        complete = directory / "already-complete.khmatch"
        run(*base, "-o", complete, "--resume", checkpoint)
        assert verify(complete, dp, digest)["matched"] == dp["q"]
        saved = directory / "first.khcp"
        saved.write_bytes(first)
        again = directory / "again.khmatch"
        run(*base, "-o", again, "--resume", saved, "--checkpoint", directory / "new.khcp")
        assert verify(again, dp, digest)["verified"] and saved.read_bytes() == first
        altered = directory / "corrupt.khcp"
        raw = bytearray(first)
        raw[-9] ^= 1
        altered.write_bytes(raw)
        rejected = directory / "rejected.khmatch"
        run(*base, "-o", rejected, "--resume", altered, expected=1)
        assert not rejected.exists()
        truncated = directory / "truncated.khcp"
        truncated.write_bytes(first[:-1])
        run(*base, "-o", rejected, "--resume", truncated, expected=1)
        assert not rejected.exists()
        reordered = copy.deepcopy(dp)
        reordered["runs"][0], reordered["runs"][1] = reordered["runs"][1], reordered["runs"][0]
        alternate = directory / "other.khdp"
        alternate.write_bytes(encode_dp(reordered))
        assert alternate.read_bytes() != source.read_bytes()
        run(sys.executable, ROOT / "match.py", alternate, "--poly", "2,3,0,1",
            "-o", rejected, "--resume", saved, expected=1)
        assert not rejected.exists()
    print("matching checkpoint and resume checks passed")


# Avoid accidental test runs when invoked without the explicit test flag.
if __name__ == "__main__":
    if sys.argv[1:] != ["--run"]:
        print("Check matching checkpoints and damaged-snapshot rejection.\nExample: python3 tests/check_checkpoints.py --run")
    else:
        check()
