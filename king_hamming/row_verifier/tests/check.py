#!/usr/bin/env python3
"""Generate small real certificates and check the independent rendered-row verifier."""

from pathlib import Path
import hashlib
import io
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from matching_solver.artifacts import Reader, load_dp, request_count

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent


def run(*command, expected=0):
    result = subprocess.run([str(item) for item in command], text=True, capture_output=True)
    if result.returncode != expected:
        raise AssertionError((command, result.returncode, result.stdout, result.stderr))
    return result


def check():
    with tempfile.TemporaryDirectory(prefix="row-verifier-") as temporary:
        temporary = Path(temporary)
        for stem in ("3_3", "5_3"):
            dp = PROJECT / "examples" / f"{stem}.khdp"
            match = temporary / f"{stem}.khmatch"
            run(sys.executable, PROJECT / "matching_solver" / "match.py", dp, "-o", match)
            result = run(ROOT / "kh_verify_rows", "--threads", "2", dp, match)
            assert "minimum_distance=" in result.stdout
            rendered = temporary / f"{stem}.rows"
            run(ROOT / "kh_verify_rows", "--render", rendered, dp, match)
            assert len(rendered.read_text().splitlines()) > 1
            run(ROOT / "kh_verify_rows", "--render", rendered, dp, match, expected=1)
            corrupt = temporary / f"{stem}.corrupt"
            raw = bytearray(match.read_bytes())
            raw[-1] ^= 1
            corrupt.write_bytes(raw)
            run(ROOT / "kh_verify_rows", dp, corrupt, expected=1)
            # Recompute the outer checksum after replacing every matching choice
            # with zero. The artifact is structurally valid but repeats endpoints.
            duplicate = temporary / f"{stem}.duplicate"
            body = bytearray(match.read_bytes()[:-32])
            document, _ = load_dp(dp)
            reader = Reader(io.BytesIO(match.read_bytes()), match.stat().st_size)
            reader.take(36)
            for _ in range(2 + document["r"] + 1 + 3):
                reader.uint()
            choice_bytes = (request_count(document) *
                            (document["f"] - 1).bit_length() + 7) // 8
            body[reader.stream.tell():reader.stream.tell() + choice_bytes] = bytes(choice_bytes)
            duplicate.write_bytes(body + hashlib.sha256(body).digest())
            run(ROOT / "kh_verify_rows", dp, duplicate, expected=1)
        run(ROOT / "kh_verify_rows", "--max-rows", "1",
            PROJECT / "examples" / "3_3.khdp", temporary / "3_3.khmatch", expected=1)
    print("independent rendered-row verification passed")


if __name__ == "__main__":
    if sys.argv[1:] == ["--run"]:
        check()
    else:
        print("Run with --run to execute the rendered-row checks.")
