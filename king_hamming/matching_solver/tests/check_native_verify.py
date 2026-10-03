#!/usr/bin/env python3
"""Differential test: kh_verify_khm1 must agree with the Python KHM1 verifier, on good and corrupted files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import NATIVE_VERIFIER, Reader, header, load_dp, publish, verify  # noqa: E402

CPU_KERNEL = HERE / "kh_match_kernel"


def certificate(path: Path, poly: str | None, directory: Path) -> tuple[dict, bytes, Path]:
    """Solve one fixture with the CPU kernel and publish its KHM1 file."""
    dp, digest = load_dp(path)
    blocks = directory / f"{path.stem}.blocks"
    blocks.write_text(f"{len(dp['runs'])}\n" + "".join(f"{r['a']} {r['t'] * r['repeat']}\n" for r in dp["runs"]))
    payload = directory / f"{path.stem}.bin"
    done = subprocess.run([str(CPU_KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(payload), "--threads", "2",
                           *(["--poly", poly] if poly else [])], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert done.returncode == 0, done.stderr[-500:]
    metadata = next(json.loads(line) for line in done.stdout.splitlines() if '"polynomial"' in line and '"status"' in line)
    output = directory / f"{path.stem}.khmatch"
    publish(output, header(dp, digest, metadata), payload)
    return dp, digest, output


def outcome(path: Path, dp: dict, digest: bytes, native: bool) -> tuple[bool, str]:
    try:
        verify(path, dp, digest, 2**31, native=native)
        return True, ""
    except ValueError as error:
        return False, str(error)


def rewrite(data: bytes, mutated: bytearray, path: Path) -> Path:
    """Write mutated content (everything but the trailing checksum) with a correct new checksum."""
    body = bytes(mutated[:-32])
    path.write_bytes(body + hashlib.sha256(body).digest())
    return path


def main() -> int:
    if "--run" not in sys.argv:
        print(__doc__ + "\nUsage: python3 tests/check_native_verify.py --run")
        return 0
    assert NATIVE_VERIFIER.exists(), "build kh_verify_khm1 first (make -C matching_solver)"
    rng = random.Random(20261002)
    with tempfile.TemporaryDirectory(prefix="native-verify-") as name:
        directory = Path(name)
        fixtures = [(ROOT / "examples/3_3.khdp", None), (ROOT / "examples/5_3.khdp", "2,3,0,1"),
                    (ROOT / "examples/7_5.khdp", None), (ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1")]
        # Binary fields use a separate fast path in the verifier: cover them from the saved campaign results.
        results = ROOT / "cluster/deployments/continuous-campaign/results"
        fixtures += [(path, None) for field in ("2_11", "2_13", "2_17") for path in sorted(results.glob(f"{field}_*.khdp"))[:1]]
        for fixture, poly in fixtures:
            dp, digest, good = certificate(fixture, poly, directory)
            python, native = verify(good, dp, digest, native=False), verify(good, dp, digest, native=True)
            assert python == native, (python, native)
            data = bytearray(good.read_bytes())
            # Corrupt only payload bytes: parse the header to find where the choices begin.
            with good.open("rb") as stream:
                reader = Reader(stream, len(data))
                reader.take(36)
                reader.uint(); reader.uint()
                for _ in range(dp["r"] + 1):
                    reader.uint()
                for _ in range(3):
                    reader.uint()
                payload_start = stream.tell()
            accepted = rejected = 0
            trials = 300 if len(data) < 200_000 else 60
            for trial in range(trials):
                mutated = bytearray(data)
                for _ in range(rng.choice([1, 1, 2, 5])):
                    position = rng.randrange(payload_start, len(data) - 32)
                    mutated[position] ^= 1 << rng.randrange(8)
                path = rewrite(bytes(data), mutated, directory / "mutated.khmatch")
                a, b = outcome(path, dp, digest, False), outcome(path, dp, digest, True)
                assert a[0] == b[0], f"{fixture.name} trial {trial}: python {a} native {b}"
                accepted += a[0]
                rejected += not a[0]
            assert rejected > 0, "corruptions were never rejected"
            print(f"ok {fixture.name}: native == python on the good file and {trials} corruptions "
                  f"({rejected} rejected, {accepted} still valid)")
        # Structural damage the Python wrapper catches before the native pass.
        for label, damage in (("truncated", lambda d: d[:-40] + d[-32:]), ("wrong checksum", lambda d: d[:-1] + bytes([d[-1] ^ 1]))):
            path = directory / "damaged.khmatch"
            path.write_bytes(damage(good.read_bytes()))
            assert not outcome(path, dp, digest, True)[0] and not outcome(path, dp, digest, False)[0], label
        print("ok structural damage rejected on both paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
