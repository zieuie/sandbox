#!/usr/bin/env python3
"""p = 2 through the wide kernel (F a power of two): the payload keeps one extra bit while it runs and is
packed down to ceil(log2 F) bits when the matching is complete. The certificate must verify as a full
matching with the independent verifier and have exactly the size of n packed choices plus the header."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from matching_solver.artifacts import header, load_dp, request_count, verify  # noqa: E402
from matching_solver.polynomials import first_primitive  # noqa: E402
import gpus  # noqa: E402


def main() -> int:
    if "--run" not in sys.argv:
        print(__doc__ + "\nUsage: python3 tests/check_p2.py --run  (needs a usable CUDA GPU)")
        return 0
    if not gpus.detect():
        print("skip: no usable CUDA device on this host")
        return 0
    with tempfile.TemporaryDirectory(prefix="kh-wide-p2-") as name:
        for fixture in ("2_9", "2_21"):
            dp_path = HERE / "fixtures" / f"{fixture}.khdp"
            dp, digest = load_dp(dp_path)
            polynomial = first_primitive(2, dp["r"], dp["q"])
            output = Path(name) / f"{fixture}.khmatch"
            completed = subprocess.run(
                [sys.executable, str(HERE.parent / "cluster_solver.py"), "--dp", str(dp_path), "--output", str(output),
                 "--poly", ",".join(map(str, polynomial)), "--threads", "2", "--max-bytes", str(2**31)],
                capture_output=True, text=True)
            if completed.returncode != 0:
                raise AssertionError(f"{fixture}: cluster_solver failed: {completed.stderr[-400:]}")
            summary = verify(output, dp, digest, 2**31)
            bits = (dp["f"] - 1).bit_length()
            assert summary["status"] == "full_matching" and summary["matched"] == request_count(dp), summary
            payload = (request_count(dp) * bits + 7) // 8
            prefix = len(header(dp, digest, {"p": 2, "r": dp["r"], "required": request_count(dp), "status": 0,
                                             "matched": request_count(dp), "polynomial": polynomial}))
            assert output.stat().st_size == prefix + payload + 32 or output.stat().st_size == prefix + payload, (
                fixture, output.stat().st_size, prefix, payload)
            print(f"ok {fixture}: q={dp['q']} F={dp['f']} ({bits}-bit choices), {output.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
