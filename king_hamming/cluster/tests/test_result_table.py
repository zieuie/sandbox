#!/usr/bin/env python3
"""Check result-table sizes, outcome precedence, and in-progress notation."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from campaigns.result_table import markdown, permutation_entries


class ResultTableTests(unittest.TestCase):
    def test_permutation_entries(self) -> None:
        self.assertEqual(permutation_entries({"theta": 1, "f": 2, "q": 8}), 108)

    def test_markdown_statuses(self) -> None:
        table = markdown({
            (2, 3): {"entries": 108, "outcomes": {0, 1}, "active": False},
            (2, 5): {"entries": 120, "outcomes": {1}, "active": False},
            (3, 3): {"entries": 256, "outcomes": set(), "active": True},
            (3, 5): {"entries": None, "outcomes": set(), "active": True},
        })
        self.assertIn("| 2 | 108^ | 120* |", table)
        self.assertIn("| 3 | 256 (running) | (running) |", table)


if __name__ == "__main__":
    unittest.main()
