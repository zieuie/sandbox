#!/usr/bin/env python3
"""Check that the campaign retries only an encountered Hall obstruction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from matching_solver import witch_hunt
from matching_solver import launch_overnight
from matching_solver.launch_overnight import MAX_BYTES, memory_required


# A synthetic verified leader outcome exercises policy without hunting examples.
def main() -> int:
    """Require one ordered retry and no duplicate submission on another pass."""
    with tempfile.TemporaryDirectory(prefix="kh-witch-policy-") as name:
        state = Path(name)
        results = state / "dp-results"
        results.mkdir()
        archive = state / "results"
        dp = ROOT / "examples/5_3.khdp"
        manifest_path = state / "manifest.json"
        original = {"p": 5, "r": 3, "q": 125, "edges": 625,
                    "poly": [2, 3, 0, 1], "dp": str(dp), "run_id": "old"}
        manifest_path.write_text(json.dumps({"leader": "test", "entries": [original]}))
        status = {"runs": [{"run_id": "old", "state": "complete", "progress_done": 124,
                            "progress_total": 125, "artifact_hash": "a" * 64,
                            "artifact_location": "http://unused"}]}
        submissions = []

        def fake_request(_leader, route, body=None):
            """Return one verified obstruction, recording any follow-up submission."""
            if route == "/v1/status":
                return status
            assert route == "/v1/enqueue"
            submissions.append(body["specification"])
            return {"run_id": "new", "state": "queued", "reused": False}

        def fake_archive(_entry, _run):
            """Represent an already verified central certificate for this test."""
            archive.mkdir(exist_ok=True)
            target = archive / "old.khmatch"
            target.write_bytes(b"verified fixture")
            return target

        with patch.object(witch_hunt, "STATE", state), \
             patch.object(witch_hunt, "MANIFEST", manifest_path), \
             patch.object(witch_hunt, "ARCHIVE", archive), \
             patch.object(witch_hunt, "RESULTS", results), \
             patch.object(witch_hunt, "request", fake_request), \
             patch.object(witch_hunt, "archive_result", fake_archive):
            first = witch_hunt.reconcile()
            second = witch_hunt.reconcile()
        saved = json.loads(manifest_path.read_text())
        assert first["retried_obstructions"] == 1
        assert second["retried_obstructions"] == 0
        assert len(submissions) == 1 and len(saved["entries"]) == 2
        assert saved["entries"][1]["poly"] == [3, 3, 0, 1]
        assert "Encountered polynomial obstructions" in (state / "STATUS.md").read_text()
    # The larger admitted frontier fits the native 2 GiB cap; the next one does not.
    def synthetic_field(q: int, f: int) -> dict:
        """Describe a full-size request graph in one compact DP run."""
        return {"q": q, "f": f, "budget": f * 5,
                "runs": [{"a": 1, "t": 1, "repeat": q // f}]}

    assert memory_required(synthetic_field(48_828_125, 3125)) < MAX_BYTES
    assert memory_required(synthetic_field(129_140_163, 6561)) > MAX_BYTES

    # Recovery replaces a dead owned agent and retains its identity for later passes.
    with tempfile.TemporaryDirectory(prefix="kh-witch-repair-") as name:
        state = Path(name)
        worker = {"host": "test-host", "root": str(state / "remote"),
                  "name": "match-test", "pid": 1, "start": "old", "port": 42000}
        leader = {"pid": os.getpid(),
                  "start": Path(f"/proc/{os.getpid()}/stat").read_text().split()[21],
                  "command": "unused"}
        (state / "manifest.json").write_text(json.dumps(
            {"leader_process": leader, "workers": [worker]}))
        replacement = {**worker, "pid": 2, "start": "new"}
        with patch.object(launch_overnight, "STATE", state), \
             patch.object(launch_overnight, "remote", return_value={"alive": False}), \
             patch.object(launch_overnight, "launch_worker", return_value=replacement), \
             patch.object(launch_overnight, "request", return_value={"ok": True}):
            outcome = launch_overnight.repair()
        assert outcome == {"recovered": ["match-test"], "errors": []}
        assert json.loads((state / "manifest.json").read_text())["workers"][0]["start"] == "new"

    # A real KHM1 travels through the streaming archive without losing bytes.
    with tempfile.TemporaryDirectory(prefix="kh-witch-archive-") as name:
        source = ROOT / "matching_solver/examples/13_5.khmatch"
        dp = ROOT / "matching_solver/examples/13_5.khdp"
        entry = {"p": 13, "r": 5, "dp": str(dp)}
        run = {"run_id": "stream",
               "artifact_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
               "artifact_location": source.resolve().as_uri()}
        with patch.object(witch_hunt, "ARCHIVE", Path(name)):
            saved = witch_hunt.archive_result(entry, run)
            assert saved.read_bytes() == source.read_bytes()
            assert witch_hunt.archive_result(entry, run) == saved
            saved.unlink()
            run["artifact_hash"] = "0" * 64
            try:
                witch_hunt.archive_result(entry, run)
            except ValueError:
                pass
            else:
                raise AssertionError("wrong download hash was accepted")
            assert not list(Path(name).iterdir())
    print("matching obstruction follow-up and streaming archive checks passed")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--run"]:
        print("Test obstruction follow-up policy.\nExample: python3 tests/check_witch_hunt.py --run")
    else:
        raise SystemExit(main())
