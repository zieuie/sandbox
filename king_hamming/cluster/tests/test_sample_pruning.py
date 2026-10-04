#!/usr/bin/env python3
"""Old resource samples are pruned at most once a minute, from an index, not on every report."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import leader


class SamplePruningTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        self.sessions = {}
        self.handler.dispatch_post("/v1/register", {"node_name": "a", "address": "http://a:8042"})
        with leader.connect(self.database) as connection:
            self.session = connection.execute("SELECT session_id FROM nodes WHERE node_name='a'").fetchone()[0]
        run = self.handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo", "arguments": {}}})["run_id"]
        job = self.handler.dispatch_post("/v1/lease", {"node_name": "a", "session_id": self.session})["job"]
        self.identity = {"run_id": run, "lease_token": job["lease_token"]}
        self.addCleanup(lambda: leader.SAMPLE_PRUNED.__setitem__(0, 0.0))
        leader.SAMPLE_PRUNED[0] = 0.0

    def report(self, cpu: int) -> None:
        self.handler.dispatch_post("/v1/resource-usage", {
            **self.identity, "component": "solver", "shard_index": 0,
            "cpu_microseconds": cpu, "peak_rss_bytes": 1})

    def stale(self, count: int) -> None:
        with leader.connect(self.database) as connection:
            connection.executemany(
                "INSERT INTO resource_usage_samples(run_id,lease_token,node_name,component,shard_index,"
                "cpu_microseconds,rss_bytes,recorded) VALUES(?,?,?,?,?,?,?,?)",
                [(self.identity["run_id"], self.identity["lease_token"], "a", "solver", 0, 1, 1,
                  time.time() - 8 * 86400) for _ in range(count)])

    def samples(self) -> int:
        with leader.connect(self.database) as connection:
            return connection.execute("SELECT COUNT(*) FROM resource_usage_samples").fetchone()[0]

    def test_week_old_samples_go_but_not_on_every_report(self) -> None:
        self.stale(5)
        self.report(10)                      # first report prunes
        self.assertEqual(self.samples(), 1)
        self.stale(5)
        self.report(20)                      # within the minute: no scan
        self.assertEqual(self.samples(), 7)
        with mock.patch.object(leader, "SAMPLE_PRUNE_SECONDS", 0.0):
            self.report(30)                  # interval elapsed: pruned again
        self.assertEqual(self.samples(), 3)

    def test_pruning_can_use_an_index(self) -> None:
        with leader.connect(self.database) as connection:
            plan = " ".join(row[3] for row in connection.execute(
                "EXPLAIN QUERY PLAN DELETE FROM resource_usage_samples WHERE recorded<?", (0.0,)))
        self.assertIn("resource_samples_recorded", plan)


if __name__ == "__main__":
    unittest.main()
