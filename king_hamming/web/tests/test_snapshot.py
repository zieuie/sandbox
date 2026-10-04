#!/usr/bin/env python3
"""Check snapshot views against small fixture deployments."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import time
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

    def test_gpu_utilization_series(self) -> None:
        connection = sqlite3.connect(self.deployments / "live" / "leader.sqlite")
        connection.execute("UPDATE nodes SET gpus_json=? WHERE node_name='dp-151'",
                           ('[{"index":0,"name":"RTX 3060","arch":86,"total_bytes":6000000000}]',))
        connection.execute("CREATE TABLE IF NOT EXISTS gpu_usage_samples (node_name TEXT, gpu_index INTEGER, "
                           "util_percent INTEGER, memory_used_bytes INTEGER, recorded REAL)")
        for age, util in ((3600, 20), (3590, 40), (30, 90)):
            connection.execute("INSERT INTO gpu_usage_samples VALUES('dp-151',0,?,?,?)", (util, 1000 + util, NOW - age))
        connection.commit()
        connection.close()
        nodes = {card["hostname"]: card for card in self.build()["fleet"]["nodes"]}
        merlin, other = nodes["merlin"], nodes["fearless"]
        self.assertEqual(len(merlin["gpu_utilization"]), len(merlin["utilization"]))
        self.assertAlmostEqual(max(merlin["gpu_utilization"]), 0.9, places=2)
        self.assertEqual(merlin["gpu_now"], {"util": 90, "memory_used_bytes": 1090})
        self.assertIsNone(other["gpu_utilization"])  # a node that reports no samples shows no graph
        self.assertIsNone(other["gpu_now"])

    def test_gpu_block_run(self) -> None:
        import base64, hashlib
        connection = sqlite3.connect(self.deployments / "live" / "leader.sqlite")
        dp = (fixture.ROOT / "examples" / "5_3.khdp").read_bytes()
        fixture.run(connection, "block-match", {"program": "match_gpu_blocks", "arguments": {
            "dp_b64": base64.b64encode(dp).decode(), "dp_sha256": hashlib.sha256(dp).hexdigest(),
            "poly": [2, 3, 0, 1], "threads": 4, "max_bytes": 2**31, "gpu_memory_bytes": 2**30}}, "complete",
            node_name="dp-151", gpu_index=0, started=NOW - 100, finished=NOW - 90,
            progress_done=125, progress_total=125,
            progress_message='{"matched":125,"required":125,"phases":3,"scans":900,"engine":"gpu-blocks",'
                             '"device":"RTX 3060","blocks":4,"rounds":2,"residual_round1":7,'
                             '"trace":[[0,125],[1,40],[2,9],[3,0]],'
                             '"seconds":{"field":0.01,"blocks":0.2,"exchange":0.1}}')
        connection.commit()
        connection.close()
        run = next(item for item in self.build()["matching"]["runs"] if item["run_id"] == "block-match")
        self.assertEqual((run["program"], run["engine"], run["outcome"]), ("match_gpu_blocks", "gpu", "matched"))
        self.assertEqual((run["gpu"]["blocks"], run["gpu"]["rounds"], run["gpu"]["residual_round1"]), (4, 2, 7))
        # The kernel's trace becomes the burndown chart's rows: [step, matched, time].
        self.assertEqual([row[:2] for row in run["phases"]], [[1, 85], [2, 116], [3, 125]])
        self.assertEqual(run["step_label"], "block, then round")

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
        # Certificates are checked off the build: "checking" first, then the outcome, kept on disk.
        first = self.build()["results"]
        checking = {(item["p"], item["r"]): item for item in first["fields"]}[(13, 5)]
        self.assertEqual(checking["matching_attempts"][0]["outcome"], "checking")
        self.snapshots.wait_for_certificates()
        self.clock.value += self.snapshots.min_refresh
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

    def test_a_finished_field_shows_its_deleted_tiles_as_done_not_as_missing_copies(self) -> None:
        """Tiles of a complete field are deleted after a retention period; they must not look under-replicated."""

        with sqlite3.connect(self.deployments / "live" / "leader.sqlite") as connection:
            connection.row_factory = sqlite3.Row
            fixture.run(connection, "done-root", {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": fixture.SIDE}},
                        "complete", finished=NOW - 600, artifact_hash="d" * 64)
            fixture.run(connection, "done-t00", fixture.tile_spec(0, 0, "done-root"), "complete", parent="done-root",
                        artifact_hash="e" * 64, started=NOW - 5000, finished=NOW - 4000)
            connection.execute("INSERT INTO distributed_tiles(parent_run_id,row,column,child_run_id) VALUES('done-root',0,0,'done-t00')")
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created) VALUES(?,3,?)", ("e" * 64, NOW))
        roots = {root["run_id"]: root for root in self.build()["roots"]}
        cell = roots["done-root"]["cells"][0]
        self.assertEqual((cell["s"], cell["rep"]), ("durable", 0))
        self.assertEqual(roots["done-root"]["counts"]["durable"], 1)
        # An unfinished root with the same missing copies still shows the tile as merely complete.
        live = roots["root-5-3"]
        self.assertTrue(any(item["s"] == "complete" for item in live["cells"]))

    def test_status_header(self) -> None:
        status = self.build()["status"]
        self.assertEqual((status["nodes_healthy"], status["nodes_total"]), (2, 3))
        self.assertEqual(status["runs"], {"running": 1, "queued": 0, "waiting": 1})
        self.assertIsNone(status["feeder"])

    def test_browsers_get_a_grid_string_and_tile_detail_on_demand(self) -> None:
        import json
        data, body, _ = self.snapshots.get()
        sent = json.loads(body)
        for internal, compact in zip(data["roots"], sent["roots"]):
            self.assertNotIn("cells", compact)
            self.assertEqual(len(compact["grid"]), compact["rows"] * compact["columns"])
            for cell in internal["cells"]:
                self.assertEqual(compact["grid"][cell["r"] * compact["columns"] + cell["c"]],
                                 snapshot.STATE_CODES[cell["s"]])
            self.assertEqual({(c["r"], c["c"]) for c in compact["live"]},
                             {(c["r"], c["c"]) for c in internal["cells"] if c["s"] in snapshot.LIVE_STATES})
        root = data["roots"][0]
        columns = json.loads(self.snapshots.root_tiles(root["run_id"])[0])
        running = next(cell for cell in root["cells"] if cell.get("node"))
        at = running["r"] * root["columns"] + running["c"]
        self.assertEqual(columns["machines"][columns["node"][at]], running["node"])
        self.assertEqual(self.snapshots.tile(root["run_id"], running["r"], running["c"])["run"], running["run"])
        self.assertIsNone(self.snapshots.root_tiles("no-such-root"))
        self.assertIsNone(self.snapshots.tile(root["run_id"], 999, 999))

    def test_certificate_checks_survive_a_restart(self) -> None:
        cache = Path(self.directory.name) / "certificates.json"
        first = snapshot.Snapshots(self.deployments, "live", clock=self.clock, certificate_cache=cache)
        first.get()
        first.wait_for_certificates()
        self.assertTrue(cache.exists())
        again = snapshot.Snapshots(self.deployments, "live", clock=self.clock, certificate_cache=cache)
        fields = {(item["p"], item["r"]): item for item in again.get()[0]["results"]["fields"]}
        self.assertEqual(fields[(13, 5)]["matching_attempts"][0]["outcome"], "matched")  # no re-hash

    def test_background_refresh_serves_the_stale_snapshot_at_once(self) -> None:
        live = snapshot.Snapshots(self.deployments, "live", clock=self.clock, background=True)
        first = live.get()[0]
        self.clock.value += live.ttl
        self.assertIs(live.get()[0], first)              # served immediately; a refresh starts
        deadline = time.monotonic() + 30
        while live.get()[0] is first and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNot(live.get()[0], first)
        live.invalidate()                                 # after a command: the next request waits
        self.assertIsNot(live.get()[0], first)

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
