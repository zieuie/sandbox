#!/usr/bin/env python3
"""Check kh_verify_wide against kh_verify_khm1 on real certificates, and on corrupted ones.

Certificates: archived campaign matchings (several primes) when the campaign directory is present,
plus two made here by kh_gpu_wide_kernel. Each must be accepted with one thread and several, in one
pass and in many, and with narrow label words (many breakpoints, as fields above 2^32 have). Each
corruption must be rejected by both verifiers.

Example: python3 tests/check_verify.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import Reader, header, load_dp, publish  # noqa: E402

WIDE = HERE / "kh_verify_wide"
KHM1 = ROOT / "matching_solver" / "kh_verify_khm1"
KERNEL = HERE / "kh_gpu_wide_kernel"
CAMPAIGN = ROOT / "cluster" / "deployments" / "continuous-campaign"
ARCHIVED = ("5^9", "19^5", "23^5", "3^15", "29^5", "11^7")


def certificate_layout(path: Path) -> tuple[list[int], int, int]:
    """(polynomial, payload offset, n) from a KHM1 header."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        reader = Reader(stream, size)
        assert reader.take(4) == b"KHM1"
        reader.take(32)
        p, r = reader.uint(), reader.uint()
        polynomial = [reader.uint() for _ in range(r + 1)]
        status, n, matched = reader.uint(), reader.uint(), reader.uint()
        assert status == 0 and matched == n, "only full matchings"
        return polynomial, stream.tell(), n


def blocks_file(directory: Path, dp: dict) -> Path:
    path = directory / f"{dp['p']}_{dp['r']}.blocks"
    path.write_text(f"{len(dp['runs'])}\n" + "".join(f"{run['a']} {run['t'] * run['repeat']}\n" for run in dp["runs"]))
    return path


def run(verifier: Path, dp: dict, polynomial: list[int], blocks: Path, path: Path, offset: int,
        *extra: str, split: int | None = None) -> tuple[bool, str]:
    environment = dict(os.environ)
    if split:
        environment["KH_VERIFY_SPLIT_BITS"] = str(split)
    done = subprocess.run([str(verifier), str(dp["p"]), str(dp["r"]), ",".join(map(str, polynomial)), str(blocks),
                           str(path), str(offset), *extra], capture_output=True, text=True, env=environment)
    return done.returncode == 0, done.stdout if done.returncode == 0 else done.stderr


def rows_for(dp: dict, cells: int, threads: int, split: int = 32) -> int:
    """--row-bytes holding `cells` rows; mirrors per_cell in src/verify_wide.c."""
    p, r = dp["p"], dp["r"]
    q, f = p ** r, p ** (r // 2)
    nbp = (q - 1) >> split
    return cells * (4 * (f + nbp) + 4 * (2 * threads + nbp + 1))


def check_certificate(name: str, dp: dict, path: Path, directory: Path, rng: random.Random) -> None:
    polynomial, offset, n = certificate_layout(path)
    blocks = blocks_file(directory, dp)
    q, f = dp["p"] ** dp["r"], dp["p"] ** (dp["r"] // 2)
    used_cells = max(run["a"] for run in dp["runs"]) * f
    ok, output = run(KHM1, dp, polynomial, blocks, path, offset)
    assert ok, f"{name}: kh_verify_khm1 rejected a good certificate: {output}"
    narrow = next(bits for bits in range(1, 33) if (q - 1) >> bits <= 64)
    cases = [((), None), (("--threads", "4"), None),
             (("--threads", "3", "--row-bytes", str(rows_for(dp, max(1, used_cells // 7), 3))), None),
             (("--threads", "2", "--row-bytes", str(rows_for(dp, max(1, used_cells // 3), 2, narrow))), narrow)]
    for extra, split in cases:
        ok, output = run(WIDE, dp, polynomial, blocks, path, offset, *extra, split=split)
        assert ok, f"{name} {extra} split={split}: rejected a good certificate: {output}"
        result = json.loads(output)
        assert result["assigned"] == result["requests"] == n, (name, result)
        if "--row-bytes" in extra:
            assert result["passes"] >= 2, (name, result)
    # Corruptions, on a copy: every one must be rejected by both verifiers.
    bits = (f - 1).bit_length()
    original = path.read_bytes()

    def corrupt(label: str, mutate) -> None:
        data = bytearray(original)
        mutate(data)
        bad = directory / f"{name}.bad"
        bad.write_bytes(bytes(data))
        for verifier, extra in ((KHM1, ()), (WIDE, ("--threads", "3", "--row-bytes",
                                                    str(rows_for(dp, max(1, used_cells // 5), 3))))):
            ok, output = run(verifier, dp, polynomial, blocks, bad, offset, *extra)
            assert not ok, f"{name}: {verifier.name} accepted a certificate with {label}"

    def set_choice(data: bytearray, index: int, value: int) -> None:
        start = offset * 8 + index * bits
        for bit in range(bits):
            byte, shift = divmod(start + bit, 8)
            data[byte] = (data[byte] & ~(1 << shift)) | (((value >> bit) & 1) << shift)

    def get_choice(index: int) -> int:
        start = offset * 8 + index * bits
        return sum(((original[(start + bit) // 8] >> ((start + bit) % 8)) & 1) << bit for bit in range(bits))

    index = rng.randrange(n)
    other = (get_choice(index) + 1 + rng.randrange(f - 1)) % f
    if n == q:  # every right is used, so any other neighbor repeats one
        corrupt("a changed choice", lambda data: set_choice(data, index, other))
    if (1 << bits) > f:
        corrupt("a choice out of range", lambda data: set_choice(data, rng.randrange(n), f))
    if (n * bits) % 8:
        def pad(data: bytearray) -> None:
            data[offset + (n * bits) // 8] |= 0x80
        corrupt("nonzero padding", pad)
    print(f"ok {name}: n={n:,}, accepted in 4 configurations, {'3' if n == q else '2'} corruptions rejected")


def main() -> int:
    if not WIDE.exists() or not KHM1.exists() or not KERNEL.exists():
        print("build first: make -C gpu_wide_match_solver all; make -C matching_solver kh_verify_khm1", file=sys.stderr)
        return 1
    rng = random.Random(20261006)
    with tempfile.TemporaryDirectory(prefix="verify-wide-check-") as temporary:
        directory = Path(temporary)
        pipeline = CAMPAIGN / "pipeline.json"
        fields = json.loads(pipeline.read_text())["fields"] if pipeline.exists() else {}
        for name in ARCHIVED:
            record = fields.get(name, {})
            archive = next((attempt.get("archive") for attempt in record.get("matching_attempts", [])
                            if attempt.get("archive")), None)
            if not archive or not record.get("dp_artifact") or not Path(archive).exists():
                print(f"skip {name}: no archived certificate here")
                continue
            dp, _ = load_dp(record["dp_artifact"])
            check_certificate(name, dp, Path(archive), directory, rng)
        # Fresh certificates from the wide solver, in several passes.
        # Block sizes as in tests/check.py; much smaller blocks leave a few requests in block 1 that
        # exchange cannot place when n = q (inherited from kh_gpu_block_kernel).
        for khdp, poly, size in ((ROOT / "examples/7_5.khdp", None, 5000),
                                 (ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1", 100000)):
            dp, digest = load_dp(khdp)
            blocks = blocks_file(directory, dp)
            payload = directory / f"{khdp.stem}.bin"
            command = [str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(payload), "--threads", "2",
                       "--block-requests", str(size)] + (["--poly", poly] if poly else [])
            done = subprocess.run(command, capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
            metadata = [json.loads(line) for line in done.stdout.splitlines() if '"status"' in line][-1]
            output = directory / f"{khdp.stem}.khmatch"
            publish(output, header(dp, digest, metadata), payload)
            check_certificate(khdp.stem, dp, output, directory, rng)
            # artifacts.verify (agent and feeder) switches to kh_verify_wide when kh_verify_khm1 can't
            # take the field: forced here by pretending q is past its 2^36 limit.
            from matching_solver import artifacts
            original_limit = artifacts.NATIVE_MAX_Q
            artifacts.NATIVE_MAX_Q = 1
            try:
                summary = artifacts.verify(output, dp, digest, 2**30)
                assert summary["verified"] and summary["matched"] == summary["required"], summary
                bad = directory / f"{khdp.stem}.flipped.khmatch"
                data = bytearray(output.read_bytes())
                data[len(data) // 2] ^= 0x10
                body = bytes(data[:-32])
                import hashlib
                bad.write_bytes(body + hashlib.sha256(body).digest())   # valid checksum, wrong choice
                try:
                    artifacts.verify(bad, dp, digest, 2**30)
                    raise AssertionError("artifacts.verify accepted a corrupted certificate via kh_verify_wide")
                except ValueError:
                    pass
            finally:
                artifacts.NATIVE_MAX_Q = original_limit
            print(f"ok artifacts.verify via kh_verify_wide: {khdp.stem}")
        shutil.rmtree(directory, ignore_errors=True)
    print("ok check_verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
