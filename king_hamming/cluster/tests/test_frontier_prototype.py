#!/usr/bin/env python3
"""Check the band-only DP prototype: exit-pointer reconstruction matches the dense solver."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver.frontier_prototype import solve


class FrontierPrototypeTests(unittest.TestCase):
    """Keeping only bands and exit pointers still reproduces the exact split."""

    def check(self, p: int, r: int, side: int) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.json"
            subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), str(p), str(r),
                            "--work-dir", str(root / "dense"), "-o", str(reference)],
                           check=True, capture_output=True)
            document, statistics = solve(p, r, side, root / "bands")
            self.assertEqual(document, json.loads(reference.read_text()), (p, r, side))
            self.assertEqual(sorted(path.name for path in (root / "bands").iterdir()
                                    if not path.name.startswith("band-")), [])
            return statistics

    def test_tiles_wider_than_the_halo(self) -> None:
        statistics = self.check(3, 9, 50)
        self.assertLess(statistics["band_cells"], statistics["cells"] // 2)
        self.check(5, 5, 32)

    def test_halo_wider_than_a_tile(self) -> None:
        self.check(3, 7, 5)
        self.check(11, 3, 23)


if __name__ == "__main__":
    unittest.main()
