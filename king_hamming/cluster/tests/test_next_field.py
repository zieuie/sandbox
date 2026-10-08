#!/usr/bin/env python3
"""campaigns/next_field.py: the quickest uncalculated field, and when there is room for it."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from campaigns import next_field  # noqa: E402

SETTINGS = {"max_tile_bytes": 13958643712, "max_tiles": 100000, "dp_threads": 16, "tile_format": 2}


class RankingTests(unittest.TestCase):
    def test_known_fields_are_skipped_and_the_quickest_comes_first(self) -> None:
        everything = [(item["p"], item["r"]) for item in next_field.candidates(set(), SETTINGS, 100 * 2**30)]
        found = next_field.candidates(set(everything[:3]), SETTINGS, 100 * 2**30)
        self.assertEqual([(item["p"], item["r"]) for item in found][:2], everything[3:5])
        self.assertEqual([item["hours"] for item in found], sorted(item["hours"] for item in found))

    def test_matchability(self) -> None:
        # 2^33: F = 65,536 is past the block matcher, and the wide matcher refuses p = 2.
        self.assertIsNotNone(next_field.matchable(2, 33, 2**33, 2**16, 10**15))
        # 3^23's 212 GB certificate needs that much disk on the matching host.
        self.assertIsNotNone(next_field.matchable(3, 23, 3**23, 3**11, 96 * 10**9))
        self.assertIsNone(next_field.matchable(41, 5, 41**5, 41**2, 96 * 10**9))

    def test_deep_halos_cost_time(self) -> None:
        # 107^3 (halo 11,449 rows) at side 512 took 8.5 h; a light field of similar work, far less.
        self.assertGreater(next_field.dp_hours(107, 3, 512, 1.61e14), 4)
        self.assertLess(next_field.dp_hours(41, 5, 4096, 3.27e14), 2)


class RoomTests(unittest.TestCase):
    def database(self, runs: list[tuple[str, int, int]], nodes: list[tuple[int, int, int | None]]):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE runs(parent_run_id, specification, state)")
        connection.execute("CREATE TABLE nodes(gpus_json, storage_free_bytes, large_free_bytes)")
        for state, p, r in runs:
            connection.execute("INSERT INTO runs VALUES(NULL,?,?)", (json.dumps(
                {"program": "dp_distributed", "arguments": {"p": p, "r": r}}), state))
        for gpu, free, large in nodes:
            connection.execute("INSERT INTO nodes VALUES(?,?,?)", (json.dumps([{"total_bytes": gpu}]), free, large))
        return connection

    def test_paused_dp_leaves_room_running_dp_does_not(self) -> None:
        self.assertTrue(next_field.room(self.database([("paused", 3, 21), ("complete", 7, 13)], []))[0])
        busy, why = next_field.room(self.database([("waiting", 41, 5)], []))
        self.assertFalse(busy)
        self.assertIn("41^5", why)

    def test_disk_is_the_largest_gpu_hosts(self) -> None:
        # gawain's 751 GB doesn't count: large matchings run on merlin (6 GB GPU).
        nodes = [(6 * 2**30, 96 * 10**9, 42 * 10**9), (4 * 2**30, 751 * 10**9, None), (2 * 2**30, 186 * 10**9, None)]
        self.assertEqual(next_field.largest_free_disk(self.database([], nodes)), 96 * 10**9)



class PendingTests(unittest.TestCase):
    def test_a_submission_waits_for_the_feeders_lock_then_enters_the_manifest(self) -> None:
        import fcntl
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "manifest.json").write_text(json.dumps({"entries": [{"run_id": "old"}]}))
            entry = {"run_id": "new", "specification": {"program": "dp_distributed", "arguments": {"p": 41, "r": 5}}}
            (state / next_field.PENDING).write_text(json.dumps([entry]))
            with (state / "pipeline.lock").open("w") as feeder:   # the feeder mid-pass
                fcntl.flock(feeder, fcntl.LOCK_EX)
                self.assertEqual(next_field.record_pending(state), 1)
            self.assertEqual(next_field.record_pending(state), 0)
            self.assertEqual([e["run_id"] for e in json.loads((state / "manifest.json").read_text())["entries"]],
                             ["old", "new"])
            self.assertFalse((state / next_field.PENDING).exists())

if __name__ == "__main__":
    unittest.main()
