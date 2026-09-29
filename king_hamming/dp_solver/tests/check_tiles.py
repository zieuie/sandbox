#!/usr/bin/env python3
"""Assemble a whole DP from bounded tile outputs and compare exact raw reference bytes."""

from __future__ import annotations

from array import array
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


# Build predecessor rectangles only from tiles already committed in this test.
def compare(p: int, r: int, side: int, threads: int) -> None:
    """Compute p^r with tile side/threads and compare all values and choices with raw C."""

    budget = p**((r + 1) // 2)
    width = budget + 1
    values = array("Q", [0]) * (width**2)
    choices = array("I", [0]) * (width**2)

    with tempfile.TemporaryDirectory(prefix="kh-tiles-") as temporary:
        root = Path(temporary)
        reference = root / "reference"
        subprocess.run([str(ROOT / "kh_dp_local"), str(p), str(r), "--raw-transitions",
                        "--work-dir", str(reference), "-o", str(root / "reference.json")],
                       check=True, capture_output=True)

        for first_u in range(1, budget + 1, side):
            last_u = min(first_u + side - 1, budget)

            for first_v in range(1, budget + 1, side):
                last_v = min(first_v + side - 1, budget)
                origin_u = max(0, first_u - p*p)
                origin_v = max(0, first_v - p*p)
                halo = array("Q")

                for u in range(origin_u, last_u + 1):
                    row = values[u*width + origin_v:u*width + last_v + 1]

                    # Uncommitted interior bytes must be ignored and deterministically replayed.
                    if u >= first_u:
                        for v in range(first_v, last_v + 1):
                            row[v - origin_v] = 0xDEADBEEF

                    halo.extend(row)

                input_path = root / "halo.bin"
                input_path.write_bytes(halo.tobytes())
                output = root / f"tile-{first_u}-{first_v}"
                subprocess.run([str(ROOT / "kh_dp_tile"), str(p), str(r), str(first_u), str(last_u),
                                str(first_v), str(last_v), str(input_path), str(output), str(threads)],
                               check=True, capture_output=True)
                result_values = array("Q")
                result_values.frombytes((output / "values.bin").read_bytes())
                result_choices = array("I")
                result_choices.frombytes((output / "choices.bin").read_bytes())
                tile_width = last_v - first_v + 1

                for offset, u in enumerate(range(first_u, last_u + 1)):
                    values[u*width + first_v:u*width + last_v + 1] = result_values[offset*tile_width:(offset+1)*tile_width]
                    choices[u*width + first_v:u*width + last_v + 1] = result_choices[offset*tile_width:(offset+1)*tile_width]

                metadata = json.loads((output / "tile.json").read_text())
                assert metadata["byteorder"] == sys.byteorder
                assert metadata["memory_payload_bytes"] <= 2*1024**3
                existing = subprocess.run([str(ROOT / "kh_dp_tile"), str(p), str(r), str(first_u), str(last_u),
                                           str(first_v), str(last_v), str(input_path), str(output)], capture_output=True)
                assert existing.returncode != 0

        assert values.tobytes() == (reference / "values.bin").read_bytes(), (p, r, "values")
        assert choices.tobytes() == (reference / "choices.bin").read_bytes(), (p, r, "choices")
        failed = subprocess.run([str(ROOT / "kh_dp_tile"), str(p), str(r), "1", "1", "1", "1",
                                 str(input_path), str(root / "rejected"), "1", "1024"], capture_output=True)
        assert failed.returncode != 0
        assert not (root / "rejected").exists()


# Empty invocation explains validation rather than launching subprocess workloads.
def main() -> int:
    """Run byte-level tile comparisons only when --run is supplied; return zero on success."""

    if "--run" not in sys.argv:
        print("Validate bounded C tiles against full raw DP.\nExample: python3 tests/check_tiles.py --run")
        return 0

    threads = min(2, len(os.sched_getaffinity(0)))
    for p, r, side in ((2, 5, 3), (3, 3, 4), (5, 3, 7), (7, 5, 128)):
        compare(p, r, side, threads)
    print("bounded tile checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
