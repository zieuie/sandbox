#!/usr/bin/env python3
"""kh_rehydrate against real certificates: the independent row verifier's rendering, formats, slices, tampering."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
TOOL = ROOT / "kh_rehydrate"
VERIFIER = PROJECT / "row_verifier" / "kh_verify_rows"


def run(*command, expected=0):
    result = subprocess.run([str(item) for item in command], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError((command, result.returncode, result.stdout[-2000:], result.stderr[-2000:]))
    return result


def check():
    if not VERIFIER.exists():
        run("make", "-C", PROJECT / "row_verifier")
    with tempfile.TemporaryDirectory(prefix="rehydrate-") as temporary:
        temporary = Path(temporary)
        for stem, rows in (("3_3", 144), ("5_3", 1375)):
            dp = PROJECT / "examples" / f"{stem}.khdp"
            match = temporary / f"{stem}.khmatch"
            run(sys.executable, PROJECT / "matching_solver" / "match.py", dp, "-o", match)

            # The text array equals the independent verifier's rendering, row for row.
            text = temporary / f"{stem}.txt"
            report = run(TOOL, "--explain", "--verify-checksum", "--output", text, dp, match).stdout
            reference = temporary / f"{stem}.ref"
            run(VERIFIER, "--render", reference, dp, match)
            assert text.read_bytes() == reference.read_bytes(), stem
            assert len(text.read_text().splitlines()) == rows, stem
            assert "RESULT: consistent" in report and "f is primitive (X generates all q-1 nonzero elements): YES" in report
            assert "equals sha256 of the rest of the file: yes" in report
            assert "whole array: rows written = N = q + F^2*theta: yes" in report
            assert (temporary / f"{stem}.txt.meta.txt").exists()

            # Binary equals text, one byte per symbol at these sizes.
            binary = temporary / f"{stem}.bin"
            run(TOOL, "--format", "bin", "--output", binary, dp, match)
            symbols = [int(x) for line in text.read_text().splitlines() for x in line.split()]
            assert list(binary.read_bytes()) == symbols, stem

            # A class range is an exact slice of the full array.
            part = temporary / f"{stem}.part"
            result = run(TOOL, "--classes", "1:3", "--output", part, dp, match).stdout
            assert "partial render" in result
            full_lines, part_lines = text.read_text().splitlines(), part.read_text().splitlines()
            assert any(full_lines[i:i + len(part_lines)] == part_lines for i in range(len(full_lines)))

            # Never overwrites; refuses outputs over the limit; requests table has one line per request.
            run(TOOL, "--output", text, dp, match, expected=1)
            tiny = temporary / f"{stem}.tiny"
            run(TOOL, "--max-bytes", "100", "--output", tiny, dp, match, expected=1)
            assert not tiny.exists()
            table = temporary / f"{stem}.requests"
            run(TOOL, "--requests", table, "--output", temporary / f"{stem}.again", dp, match)
            assert len(table.read_text().splitlines()) > 1

            # Corruption and a wrong DP are caught.
            raw = bytearray(match.read_bytes())
            raw[len(raw) // 2] ^= 1
            damaged = temporary / f"{stem}.damaged"
            damaged.write_bytes(raw)
            assert "RESULT: PROBLEMS" in run(TOOL, "--verify-checksum", dp, damaged, expected=1).stdout
            other = PROJECT / "examples" / ("5_3.khdp" if stem == "3_3" else "3_3.khdp")
            assert "equals the DP file's sha256: NO" in run(TOOL, other, match, expected=1).stdout

        # Discovery by hash in a campaign-shaped directory.
        state = temporary / "state"
        (state / "results").mkdir(parents=True)
        (state / "matching-results").mkdir()
        shutil.copy(PROJECT / "examples" / "3_3.khdp", state / "results" / "3_3_aaaa.khdp")
        shutil.copy(PROJECT / "examples" / "5_3.khdp", state / "results" / "3_3_zzzz.khdp")  # a decoy
        shutil.copy(temporary / "3_3.khmatch", state / "matching-results" / "3_3_bbbb.khmatch")
        found = run(TOOL, "--state", state, "--field", "3,3")
        assert "3_3_aaaa.khdp" in found.stderr and "RESULT: consistent" in found.stdout
    print("ok kh_rehydrate: rendering identical to row_verifier for 3^3 and 5^3, formats, slices, guards, tampering, discovery")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "--run":
        print(__doc__)
    else:
        check()
