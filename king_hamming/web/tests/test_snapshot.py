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

    def test_gpu_fields(self) -> None:
        connection = sqlite3.connect(self.deployments / "live" / "leader.sqlite")
        connection.execute("UPDATE nodes SET gpus_json=? WHERE node_name='dp-151'",
                           ('[{"index":0,"name":"RTX 3060","arch":86,"total_bytes":6000000000}]',))
        connection.execute("UPDATE runs SET progress_details='{\"engine\":\"gpu\"}' WHERE run_id='t00'")
        dp = (fixture.ROOT / "examples" / "5_3.khdp").read_bytes()
        import base64, hashlib
        fixture.run(connection, "gpu-match", {"program": "match_gpu", "arguments": {
            "dp_b64": base64.b64encode(dp).decode(), "dp_sha256": hashlib.sha256(dp).hexdigest(),
            "poly": [2, 3, 0, 1], "threads": 4, "max_bytes": 2**31}}, "complete",
            node_name="dp-151", gpu_index=0, started=NOW - 100, finished=NOW - 90,
            progress_done=125, progress_total=125,
            progress_message='{"matched":125,"required":125,"phases":2,"scans":900,"engine":"gpu",'
                             '"device":"RTX 3060","seconds":{"field":0.01,"greedy":0.002,"augment":0.003}}')
        connection.commit()
        connection.close()
        built = self.build()
        nodes = {card["hostname"]: card for card in built["fleet"]["nodes"]}
        self.assertEqual(nodes["merlin"]["gpus"][0]["name"], "RTX 3060")
        self.assertEqual(nodes["fearless"]["gpus"], [])
        root = built["roots"][0]
        self.assertEqual(root["gpu_tiles"], 1)
        self.assertEqual({(c["r"], c["c"]) for c in root["cells"] if c.get("gpu")}, {(0, 0)})
        run = next(item for item in built["matching"]["runs"] if item["run_id"] == "gpu-match")
        self.assertEqual(run["engine"], "gpu")
        self.assertEqual((run["gpu"]["index"], run["gpu"]["device"], run["gpu"]["phases"]), (0, "RTX 3060", 2))
        self.assertEqual(run["outcome"], "matched")
        self.assertIsNone(snapshot.node_gpus({"node_name": "old-leader-row"}))

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

    def test_cancelled_dp_has_its_own_status(self) -> None:
        with sqlite3.connect(self.deployments / "older" / "leader.sqlite") as connection:
            fixture.run(connection, "dp117", {"program": "dp_distributed",
                                              "arguments": {"p": 11, "r": 7, "tile_side": 8}},
                        "cancelled", finished=NOW - 50_000)
        fields = {(item["p"], item["r"]): item for item in self.build()["results"]["fields"]}
        self.assertEqual(fields[(11, 7)]["status"], "dp_cancelled")
        self.assertEqual(fields[(7, 5)]["status"], "dp_failed")  # a failure still wins

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
