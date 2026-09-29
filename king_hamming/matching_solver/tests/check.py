#!/usr/bin/env python3
"""Check C matching, KHM1 compatibility, corruption rejection, and resource controls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(PROJECT))
from matching_solver.artifacts import load_dp, request_count, verify


# Treat the standalone Python reader as an independent interpretation of KHM1.
def reference_reader():
    """Return the independent artifact reader module."""
    location = PROJECT / "scripts" / "inspect_artifact.py"
    spec = importlib.util.spec_from_file_location("reference_artifact_reader", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Invoke a CLI and assert its advertised exit outcome.
def run(*command, expected=0):
    """Run input argv and return captured result, rejecting unexpected status."""
    result = subprocess.run([str(arg) for arg in command], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError((command, result.returncode, result.stdout, result.stderr))
    return result


# Ensure invalid data is rejected rather than reported as a valid matching.
def rejects(action):
    """Invoke callable action and require an exception; return no value."""
    try:
        action()
    except (ValueError, OSError):
        return
    raise AssertionError("invalid certificate accepted")


# Exercise end-to-end exact matching on small fixed fields and malformed inputs.
def check():
    """Run independent matching checks; return no value on success."""
    reference = reference_reader()
    with tempfile.TemporaryDirectory(prefix="matching-check-") as temporary:
        directory = Path(temporary)
        for stem in ("3_3", "5_3", "7_5"):
            dp_path = PROJECT / "examples" / f"{stem}.khdp"
            dp, digest = load_dp(dp_path)
            output = directory / f"{stem}.khmatch"
            result = run(sys.executable, ROOT / "match.py", dp_path, "-o", output)
            summary = json.loads(result.stdout)
            assert summary["verified"] and summary["required"] == request_count(dp)
            assert summary["matched"] == summary["required"]
            assert verify(output, dp, digest)["verified"]
            parallel_threads = min(4, len(os.sched_getaffinity(0)))
            if parallel_threads > 1:
                parallel = directory / f"{stem}.parallel.khmatch"
                run(sys.executable, ROOT / "match.py", dp_path, "-o", parallel,
                    "--threads", parallel_threads)
                assert verify(parallel, dp, digest)["verified"]
                parallel_reference = reference.read_matching(parallel.read_bytes(),
                    reference.read_dp(dp_path.read_bytes()), verify=True)
                assert parallel_reference["matched"] == summary["required"]
            old_dp = reference.read_dp(dp_path.read_bytes())
            old_matching = reference.read_matching(output.read_bytes(), old_dp, verify=True, hydrate=True)
            assert old_matching["construction_verified"] and old_matching["required"] == summary["required"]
            hydrate = directory / f"{stem}.tsv"
            run(sys.executable, ROOT / "verify_match.py", output, "--dp", dp_path, "--hydrate", hydrate)
            assert len(hydrate.read_text().splitlines()) == summary["required"] + 1
            run(sys.executable, ROOT / "match.py", dp_path, "-o", output, expected=1)
            limited = directory / f"{stem}.limited"
            run(sys.executable, ROOT / "match.py", dp_path, "-o", limited, "--max-bytes", "1024", expected=1)
            assert not limited.exists()
            malformed = directory / f"{stem}.corrupt"
            raw = bytearray(output.read_bytes())
            raw[-33] ^= 1
            malformed.write_bytes(raw)
            rejects(lambda: verify(malformed, dp, digest))
            duplicate = directory / f"{stem}.duplicate"
            body = bytearray(output.read_bytes()[:-32])
            cursor = 36
            reader = reference.Reader(output.read_bytes())
            reader.take(36)
            for _ in range(2 + dp["r"] + 1 + 3):
                reader.uint()
            cursor = reader.pos
            size = (request_count(dp) * (dp["f"] - 1).bit_length() + 7) // 8
            body[cursor:cursor + size] = bytes(size)
            duplicate.write_bytes(body + hashlib.sha256(body).digest())
            rejects(lambda: verify(duplicate, dp, digest))
        # A second extension degree exercises a different bit width and field size.
        tiny_dp = directory / "2_5.khdp"
        tiny_json = directory / "2_5.json"
        run(PROJECT / "dp_solver" / "kh_dp_local", "2", "5", "--work-dir",
            directory / "2_5.work", "-o", tiny_json)
        run(sys.executable, PROJECT / "dp_solver" / "print_dp.py", tiny_json,
            "--binary-out", tiny_dp)
        tiny_match = directory / "2_5.khmatch"
        run(sys.executable, ROOT / "match.py", tiny_dp, "-o", tiny_match)
        tiny_document, tiny_digest = load_dp(tiny_dp)
        assert verify(tiny_match, tiny_document, tiny_digest)["matched"] == request_count(tiny_document)
        bad = directory / "invalid-poly"
        run(sys.executable, ROOT / "match.py", PROJECT / "examples" / "5_3.khdp",
            "-o", bad, "--poly", "2,2,0,1", expected=1)
        assert not bad.exists()
    print("matching kernel and certificate checks passed")


# Do not run tests accidentally when the utility is invoked with no arguments.
if __name__ == "__main__":
    if sys.argv[1:] != ["--run"]:
        print("Check matching, KHM1 compatibility, and rejection paths.\nExample: python3 tests/check.py --run")
    else:
        check()
