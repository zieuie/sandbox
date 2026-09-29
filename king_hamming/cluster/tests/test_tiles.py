#!/usr/bin/env python3
"""Validate immutable dependency halos against the exact dense reference DP."""

from __future__ import annotations

from array import array
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT.parent))
from dp_solver.tiles import build_halo, dependencies, tile


# Exercise dependency readiness, native assembly, and independent full-state comparisons.
class TileTests(unittest.TestCase):
    """Check the predecessor cover without materializing a whole working DP in the assembler."""

    def test_wave_dependencies_and_complete_state(self) -> None:
        """Compute a clipped multi-wave DP solely from earlier immutable output tiles."""

        p, r, side = 5, 3, 7
        budget = 25
        width = budget + 1
        values = array("Q", [0]) * width**2
        choices = array("I", [0]) * width**2
        count = (budget + side - 1) // side
        sources = {}
        workers = min(2, len(os.sched_getaffinity(0)))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for wave in range(2*count - 1):
                for row in range(count):
                    column = wave - row
                    if column < 0 or column >= count:
                        continue
                    target = tile(p, r, side, row, column)
                    predecessors = dependencies(p, r, side, target)
                    self.assertTrue(all(item.row + item.column < wave for item in predecessors))
                    halo = root / f"halo-{row}-{column}.bin"
                    build_halo(p, r, side, target, sources, halo)
                    output = root / f"tile-{row}-{column}"
                    subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_tile"), str(p), str(r),
                                    str(target.first_u), str(target.last_u), str(target.first_v), str(target.last_v),
                                    str(halo), str(output), str(workers)], check=True, capture_output=True)
                    sources[(row, column)] = output / "values.bin"
                    tile_values = array("Q")
                    tile_values.frombytes((output / "values.bin").read_bytes())
                    tile_choices = array("I")
                    tile_choices.frombytes((output / "choices.bin").read_bytes())
                    tile_width = target.last_v - target.first_v + 1
                    for offset, u in enumerate(range(target.first_u, target.last_u + 1)):
                        values[u*width + target.first_v:u*width + target.last_v + 1] = tile_values[offset*tile_width:(offset+1)*tile_width]
                        choices[u*width + target.first_v:u*width + target.last_v + 1] = tile_choices[offset*tile_width:(offset+1)*tile_width]

            reference = root / "reference"
            subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), str(p), str(r), "--raw-transitions",
                            "--work-dir", str(reference), "-o", str(root / "reference.json")], check=True, capture_output=True)
            self.assertEqual(values.tobytes(), (reference / "values.bin").read_bytes())
            self.assertEqual(choices.tobytes(), (reference / "choices.bin").read_bytes())

    def test_sparse_halo_cover_and_admission(self) -> None:
        """Far tiles need a bounded neighboring cover and invalid predecessor sizes fail early."""

        target = tile(3, 5, 4, 6, 6)
        cover = dependencies(3, 5, 4, target)
        self.assertNotIn((0, 0), [(item.row, item.column) for item in cover])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "halo"
            with self.assertRaises(ValueError):
                build_halo(3, 5, 4, target, {}, output, max_bytes=1)
            self.assertFalse(output.exists())
            sources = {}
            for item in cover:
                source = Path(temporary) / f"{item.row}-{item.column}"
                source.write_bytes(b"wrong")
                sources[(item.row, item.column)] = source
            with self.assertRaises(ValueError):
                build_halo(3, 5, 4, target, sources, output)
            self.assertFalse(output.exists())


# Empty invocation prints a useful example without performing tile computations.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Validate distributed tile dependencies.\nExample: python3 tests/test_tiles.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
