#!/usr/bin/env python3
"""Check snapshot views against small fixture deployments."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import fixture
from fixture import NOW
import snapshot


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.deployments = Path(self.directory.name)
        fixture.live_campaign(self.deployments)
        fixture.results_campaign(self.deployments)
        self.clock = Clock(NOW)
        self.snapshots = snapshot.Snapshots(self.deployments, "live", clock=self.clock)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self) -> dict:
        return self.snapshots.get(force=True)[0]

    def test_tile_states_and_boundary(self) -> None:
        roots = self.build()["roots"]
        self.assertEqual(len(roots), 1)
        root = roots[0]
        states = {(cell["r"], cell["c"]): cell["s"] for cell in root["cells"]}
        self.assertEqual(states, {(0, 0): "durable", (0, 1): "ready", (0, 2): "complete",
                                  (1, 0): "running", (1, 1): "blocked"})
        self.assertEqual(root["boundary"], "1,1 waits for 0,1")
        self.assertEqual((root["rows"], root["columns"]), (2, 3))
        running = next(cell for cell in root["cells"] if cell["s"] == "running")
        self.assertEqual(running["node"], "fearless")
        self.assertEqual((running["done"], running["total"]), (25, 100))
        self.assertEqual(root["median_tile_seconds"], 450)

    def test_fleet_cards(self) -> None:
        nodes = {card["hostname"]: card for card in self.build()["fleet"]["nodes"]}
        self.assertEqual(list(nodes), ["fearless", "showgirl", "merlin"])
        fearless = nodes["fearless"]
        self.assertEqual([cpu["state"] for cpu in fearless["cpus"]], ["busy", "busy", "free", "free"])
        work = fearless["work"][0]
        self.assertEqual((work["field"], work["label"], work["health"]), ([5, 3], "tile 1,0", "responding"))
        self.assertFalse(work["orphaned"])
        self.assertEqual(fearless["reserved_memory_bytes"], 2 * 1024**3)
        self.assertEqual(nodes["showgirl"]["state"], "unavailable")
        self.assertEqual(nodes["showgirl"]["idle_reason"], "unavailable")
        # merlin's CPUs 0 and 4 are outside its schedulable set
        self.assertEqual([cpu["state"] for cpu in nodes["merlin"]["cpus"]],
                         ["reserved", "free", "free", "free", "reserved", "free", "free", "free"])

    def test_utilization_and_busy_fraction(self) -> None:
        fearless = next(card for card in self.build()["fleet"]["nodes"] if card["name"] == "dp-101")
        cpu_seconds = sum(fearless["utilization"]) * snapshot.BUCKET_SECONDS * 4
        self.assertAlmostEqual(cpu_seconds, 120, delta=1)
        self.assertAlmostEqual(fearless["busy_fraction"], 3600 / 86400, places=3)

    def test_failed_root_with_running_tiles_stays_visible(self) -> None:
        with sqlite3.connect(self.deployments / "live" / "leader.sqlite") as connection:
            connection.execute("UPDATE runs SET state='failed',finished=?,error='tile 0,1: timed out' "
                               "WHERE run_id='root-5-3'", (NOW - 30,))
        data = self.build()
        root = data["roots"][0]
        self.assertTrue(root["active"])
        self.assertEqual(root["orphaned_children"], 1)
        self.assertEqual({cell["s"] for cell in root["cells"] if cell["r"] == 0 and cell["c"] == 1},
                         {"unscheduled"})
        work = next(card for card in data["fleet"]["nodes"] if card["name"] == "dp-101")["work"][0]
        self.assertTrue(work["orphaned"])
        self.assertEqual(work["root_state"], "failed")

    def test_results(self) -> None:
        results = self.build()["results"]
        fields = {(item["p"], item["r"]): item for item in results["fields"]}
        self.assertEqual(fields[(5, 3)]["status"], "too_big")  # pipeline says field limit
        self.assertEqual(fields[(5, 3)]["metrics"]["rows"], 1375)
        self.assertEqual(fields[(13, 5)]["status"], "matched")
        self.assertEqual(fields[(13, 5)]["matching_attempts"][0]["outcome"], "matched")
        self.assertEqual(fields[(3, 3)]["status"], "unknown")
        self.assertIn("older: completed DP not collected", fields[(3, 3)]["notes"])
        self.assertEqual(fields[(7, 5)]["status"], "dp_failed")
        self.assertEqual(fields[(7, 5)]["dp_attempts"][0]["error"], "tile 1,1: timed out")
        live = {item["name"]: item["live"] for item in results["deployments"]}
        self.assertEqual(live, {"live": True, "older": False})
        states = {item["deployment"]: (item["state"], item["active"])
                  for item in fields[(5, 3)]["dp_attempts"]}
        self.assertEqual(states, {"live": ("waiting", True), "older": ("complete", False)})

    def test_status_header(self) -> None:
        status = self.build()["status"]
        self.assertEqual((status["nodes_healthy"], status["nodes_total"]), (2, 3))
        self.assertEqual(status["runs"], {"running": 1, "queued": 0, "waiting": 1})
        self.assertIsNone(status["feeder"])

    def test_cache_and_forced_refresh(self) -> None:
        first = self.snapshots.get()[0]
        self.clock.value += 1
        self.assertIs(self.snapshots.get()[0], first)
        self.assertIs(self.snapshots.get(force=True)[0], first)  # inside min_refresh
        self.clock.value += self.snapshots.min_refresh
        self.assertIsNot(self.snapshots.get(force=True)[0], first)
        second = self.snapshots.get()[0]
        self.clock.value += self.snapshots.ttl
        self.assertIsNot(self.snapshots.get()[0], second)

    def test_failed_section_keeps_previous_data(self) -> None:
        first = self.build()
        self.clock.value += 60

        def broken(*arguments):
            raise RuntimeError("boom")

        self.snapshots.build_fleet = broken
        second = self.build()
        self.assertEqual(second["stale"], ["fleet"])
        self.assertEqual(second["fleet"], first["fleet"])
        self.assertIn("boom", second["warnings"][0]["message"])
        self.assertIsNotNone(second["roots"])

    def test_never_writes(self) -> None:
        path = self.deployments / "live" / "leader.sqlite"
        before = path.stat().st_mtime_ns
        self.build()
        self.assertEqual(path.stat().st_mtime_ns, before)
        with self.assertRaises(sqlite3.OperationalError):
            snapshot.open_read_only(path).execute("DELETE FROM runs")


if __name__ == "__main__":
    unittest.main()
