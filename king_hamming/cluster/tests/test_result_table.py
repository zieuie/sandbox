#!/usr/bin/env python3
"""Check result-table row counts, outcome precedence, and in-progress notation."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from campaigns.result_table import markdown, permutation_count


class ResultTableTests(unittest.TestCase):
    def test_permutation_count(self) -> None:
        self.assertEqual(permutation_count({"theta": 1, "f": 2, "q": 8}), 12)

    def test_markdown_statuses(self) -> None:
        table = markdown({
            (2, 3): {"rows": 12, "outcomes": {0, 1}, "active": False},
            (2, 5): {"rows": 120, "outcomes": {1}, "active": False},
            (3, 3): {"rows": 256, "outcomes": set(), "active": True},
            (3, 5): {"rows": None, "outcomes": set(), "active": True},
        })
        self.assertIn("| 2 | 12^ | 120* |", table)
        self.assertIn("| 3 | 256 (running) | (running) |", table)

    def test_every_matching_program_counts(self) -> None:
        # GPU-matched fields (13^9 among them) once showed no ^ because this list only had
        # the CPU programs; it must cover every program the dashboard treats as matching.
        from campaigns.result_table import MATCH_PROGRAMS
        sys.path.insert(0, str(ROOT / "web"))
        import snapshot
        self.assertEqual(set(MATCH_PROGRAMS), set(snapshot.MATCH_PROGRAMS))


if __name__ == "__main__":
    unittest.main()
