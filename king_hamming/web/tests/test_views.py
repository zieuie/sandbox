#!/usr/bin/env python3
"""Check the log watcher, timeline, feeder panel and problems feed."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import fixture
from fixture import NOW
from logs import FeederLogParser, LeaderLogParser, LogWatcher
import snapshot


class LogWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "leader.log"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_baseline_is_current_session_only_and_new_lines_are_timed(self) -> None:
        self.path.write_text(
            "leader listening on old\n"
            "sqlite3.OperationalError: database is locked\n"
            "leader listening on new\n" + fixture.LOCKED_BLOCK)
        watcher = LogWatcher(self.path, LeaderLogParser())
        watcher.poll(100.0)
        kinds = [(event["kind"], event.get("type")) for event in watcher.baseline]
        self.assertEqual(kinds, [("leader_start", None),
                                 ("leader_exception", "sqlite3.OperationalError")])
        self.assertEqual(watcher.baseline[1]["client"], "192.168.4.101")
        self.assertIsNone(watcher.baseline[1]["time"])
        self.assertFalse(watcher.baseline[1]["benign"])

        with self.path.open("a") as stream:
            stream.write(fixture.LOCKED_BLOCK + "Exception occurred during processing of request from ('x', 1)\n")
        watcher.poll(200.0)
        self.assertEqual(len(watcher.observed), 1)
        self.assertEqual((watcher.observed[0]["time"], watcher.observed[0]["after"]), (200.0, 100.0))

        # A block finishes only when its separator line arrives.
        with self.path.open("a") as stream:
            stream.write("BrokenPipeError: [Errno 32] Broken pipe\n" + "-" * 40)
        watcher.poll(300.0)
        self.assertEqual(len(watcher.observed), 1)
        with self.path.open("a") as stream:
            stream.write("\n")
        watcher.poll(400.0)
        self.assertEqual(len(watcher.observed), 2)
        self.assertTrue(watcher.observed[1]["benign"])

    def test_rotation_restarts_from_the_beginning(self) -> None:
        self.path.write_text("leader listening on a\n" + fixture.LOCKED_BLOCK * 3)
        watcher = LogWatcher(self.path, LeaderLogParser())
        watcher.poll(1.0)
        self.path.unlink()
        self.path.write_text(fixture.LOCKED_BLOCK)
        watcher.poll(2.0)
        self.assertEqual(len(watcher.observed), 1)

    def test_feeder_parser(self) -> None:
        events = FeederLogParser().feed([
            '{"collected_dp": 1, "matching": {"submitted": 1}}',
            "continuous campaign will retry: <urlopen error refused>",
            "unrelated"])
        self.assertEqual([event["kind"] for event in events], ["reconcile", "feeder_error"])
        self.assertEqual(events[1]["message"], "<urlopen error refused>")


class ViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.deployments = Path(self.directory.name)
        fixture.live_campaign(self.deployments)
        fixture.feeder_state(self.deployments)
        self.database = self.deployments / "live" / "leader.sqlite"
        with sqlite3.connect(self.database) as connection:
            for token, run, node, started, finished, outcome in (
                    ("l-a", "t00", "dp-151", NOW - 6000, NOW - 5400, "complete"),
                    ("l-b", "t02", "dp-151", NOW - 5395, NOW - 4700, "complete"),
                    ("l-c", "t02", "dp-151", NOW - 5000, NOW - 4900, "engine retry"),
                    ("l-d", "t00", "dp-101", NOW - 9000, NOW - 8000, "lease expired")):
                connection.execute(
                    "INSERT INTO lease_history(lease_token,run_id,node_name,attempt,started,finished,outcome) "
                    "VALUES(?,?,?,1,?,?,?)", (token, run, node, started, finished, outcome))
        self.clock = [NOW]
        self.snapshots = snapshot.Snapshots(self.deployments, "live", clock=lambda: self.clock[0])

    def tearDown(self) -> None:
        self.directory.cleanup()

    def build(self) -> dict:
        self.clock[0] += 5  # past min_refresh, within the 60 s lease
        return self.snapshots.get(force=True)[0]

    def test_timeline_merges_and_stacks(self) -> None:
        segments = self.build()["timeline"]["segments"]
        merlin = sorted((s for s in segments if s["n"] == "dp-151"), key=lambda s: s["t0"])
        self.assertEqual([(s["c"], s["o"], s["lane"]) for s in merlin],
                         [(2, "ok", 0), (1, "retry", 1)])
        self.assertEqual((merlin[0]["a"], merlin[0]["b"]), ("tile 0,0", "tile 0,2"))
        running = next(s for s in segments if s["o"] == "running")
        self.assertEqual((running["n"], running["f"], running["k"], running["t1"]),
                         ("dp-101", "5,3", "tile", None))
        nodes = {row["name"]: row["lanes"] for row in self.build()["timeline"]["nodes"]}
        self.assertEqual(nodes["dp-151"], 2)

    def test_feeder_panel(self) -> None:
        feeder = self.build()["feeder"]
        self.assertFalse(feeder["process"]["alive"])
        self.assertEqual(feeder["process"]["interval"], 120)
        given_up = {tuple(item["field"]): item["dp_given_up"] for item in feeder["in_flight"]}
        self.assertEqual(given_up, {(5, 3): False, (97, 3): True})
        progress = next(item for item in feeder["in_flight"] if item["field"] == [5, 3])["dp_progress"]
        self.assertEqual((progress["done"], progress["total"]), (2, 5))
        self.assertTrue(feeder["upcoming"])
        self.assertNotIn([5, 3], [item["field"] for item in feeder["upcoming"]])
        kinds = [entry["kind"] for entry in feeder["history"]]
        self.assertEqual(kinds, ["reconcile", "feeder_error", "reconcile"])

    def test_problems_now(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='failed',finished=?,error='tile 0,1: timed out' "
                               "WHERE run_id='root-5-3'", (NOW - 30,))
        problems = self.build()["problems"]
        groups = {entry["group"]: entry for entry in problems["active"]}
        self.assertEqual(groups["feeder_down"]["severity"], "critical")
        self.assertEqual(groups["gave_up:97,3"]["severity"], "critical")
        self.assertEqual(groups["node_down:dp-108"]["severity"], "critical")
        self.assertEqual(groups["orphaned:5,3"]["severity"], "warning")
        self.assertNotIn("feeder_failing", groups)  # the last feeder line succeeded
        self.assertEqual(problems["counts"]["critical"], 3)
        self.assertEqual(problems["active"][0]["severity"], "critical")

    def test_problem_events(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='failed',finished=?,error='tile 0,1: timed out' "
                               "WHERE run_id='root-5-3'", (NOW - 30,))
        events = self.build()["problems"]["events"]
        by_group = {}
        for event in events:
            by_group.setdefault(event["group"], []).append(event)
        root = by_group["dp_root:5,3"][0]
        self.assertEqual((root["severity"], root["title"]), ("warning", "5³ DP attempt 1 of 3 failed"))
        self.assertEqual(by_group["engine_retry:5,3"][0]["severity"], "info")
        self.assertIn("fearless", by_group["lease_expired:dp-101"][0]["title"])
        # Only the current leader session; its broken pipe is benign info.
        log = [event for event in events if event["group"].startswith("leader_log:")]
        self.assertEqual([(event["severity"], event["time"]) for event in log], [("info", None)])
        self.assertEqual(log[0]["detail"], "while serving merlin")
        self.assertEqual(len(by_group["feeder_error:<urlopen error [Errno 101] Network is unreachable>"]), 1)

    def test_recent_leader_errors_become_active(self) -> None:
        self.build()
        with (self.deployments / "live" / "leader.log").open("a") as stream:
            stream.write(fixture.LOCKED_BLOCK * 2)
        problems = self.build()["problems"]
        recent = [entry for entry in problems["active"] if entry["group"].startswith("leader_recent:")]
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["count"], 2)
        self.assertIn("database is locked", recent[0]["title"])

    def test_matching_runs_and_phases(self) -> None:
        import base64
        import hashlib
        import json as json_module
        dp_raw = (fixture.ROOT / "matching_solver" / "examples" / "13_5.khdp").read_bytes()
        specification = {"program": "match_distributed", "arguments": {
            "dp_b64": base64.b64encode(dp_raw).decode(), "dp_sha256": hashlib.sha256(dp_raw).hexdigest(),
            "poly": [2, 4, 0, 0, 0, 1], "workers": 2}}
        with sqlite3.connect(self.database) as connection:
            fixture.run(connection, "m1", specification, "running", node_name="dp-101", started=NOW - 600,
                        progress_done=900, progress_total=1000, last_solver_heartbeat=NOW,
                        last_progress_at=NOW)
            fixture.run(connection, "m0", {**specification, "arguments": {**specification["arguments"],
                                                                          "poly": [3, 1, 0, 0, 0, 1]}},
                        "complete", node_name="dp-151", started=NOW - 9000, finished=NOW - 8000,
                        progress_done=990, progress_total=1000)
            for run_id, cursor, done in (("m1", 1, 700), ("m1", 2, 850), ("m1", 2, 860), ("m0", 1, 990)):
                connection.execute(
                    "INSERT INTO checkpoints(manifest_hash,run_id,cursor,done,manifest,created) VALUES(?,?,?,?,?,?)",
                    (f"{run_id}-{cursor}-{done}", run_id, cursor, done, "{}", NOW - 100 + done / 1000))
            connection.execute("INSERT INTO node_reservations(node_name,run_id,lease_token,created) "
                               "VALUES('dp-151','m1','lease-m1',?)", (NOW,))
            connection.execute(
                "INSERT INTO resource_usage(run_id,lease_token,node_name,component,shard_index,"
                "cpu_microseconds,peak_rss_bytes,recorded) VALUES('m0','l0','dp-151','coordinator',-1,5000000,1024,?)",
                (NOW,))
        runs = {run["run_id"]: run for run in self.build()["matching"]["runs"]}
        live, done = runs["m1"], runs["m0"]
        self.assertEqual(list(runs)[0], "m1")  # active first
        self.assertEqual(live["field"], [13, 5])
        self.assertEqual(live["phases"], [[1, 700, live["phases"][0][2]], [2, 860, live["phases"][1][2]]])
        self.assertEqual(live["machines"], ["fearless", "merlin"])
        self.assertIsNone(live["outcome"])
        self.assertEqual((done["outcome"], done["machines"]), ("obstructed", ["merlin"]))
        self.assertEqual(done["resources"][0]["cpu_seconds"], 5.0)
        json_module.dumps(runs)

    def test_feeder_failing_when_last_pass_errored(self) -> None:
        with (self.deployments / "live" / "feeder.log").open("a") as stream:
            stream.write("continuous campaign will retry: boom\n")
        groups = {entry["group"] for entry in self.build()["problems"]["active"]}
        self.assertIn("feeder_failing", groups)


if __name__ == "__main__":
    unittest.main()
