#!/usr/bin/env python3
"""Recover durable distributed DP tiles across otherwise independent attempts."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import leader
from dp_solver import distributed


SPECIFICATION = {"program": "dp_distributed", "arguments": {
    "p": 5, "r": 3, "tile_side": 7, "threads": 1,
}}
HASH = "a" * 64


class TileReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name in ("a", "b"):
            self.handler.dispatch_post("/v1/register", {
                "node_name": name, "address": f"http://{name}:8042",
            })

    def enqueue(self, **options):
        return self.handler.dispatch_post("/v1/enqueue", {
            "specification": SPECIFICATION, **options,
        })["run_id"]

    def child(self, parent: str):
        with leader.connect(self.database) as connection:
            return connection.execute(
                "SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                "WHERE t.parent_run_id=? AND t.row=0 AND t.column=0", (parent,),
            ).fetchone()

    def complete_old_tile(self, parent: str, replicas: int = 2) -> None:
        child = self.child(parent)
        self.assertEqual(child["state"], "queued")
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) "
                               "VALUES(?,2,?,9)", (HASH, now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=?,finished=? "
                               "WHERE run_id=?", (HASH, now, child["run_id"]))
            for name in ("a", "b")[:replicas]:
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                                   "VALUES(?,?,?,?)", (HASH, name, f"http://{name}/blob", now))
            connection.execute("UPDATE runs SET state='failed',finished=? WHERE run_id=?", (now, parent))

    def test_new_attempt_reuses_two_replica_tile(self) -> None:
        old = self.enqueue()
        original = self.child(old)
        self.complete_old_tile(old)
        current = self.enqueue(rerun=True)
        reused = self.child(current)
        self.assertNotEqual(reused["run_id"], original["run_id"])
        self.assertEqual(reused["parent_run_id"], current)
        self.assertEqual(reused["state"], "complete")
        self.assertEqual(reused["artifact_hash"], HASH)
        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT progress_done FROM runs WHERE run_id=?", (current,),
            ).fetchone()[0], 49)
            distributed.advance(connection, time.time())
        self.assertEqual(self.child(current)["run_id"], reused["run_id"])

    def test_active_retry_imports_late_replica_without_revoking_running_tile(self) -> None:
        old = self.enqueue()
        self.complete_old_tile(old, replicas=1)
        current = self.enqueue(rerun=True)
        queued = self.child(current)
        self.assertEqual(queued["state"], "queued")
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                               "VALUES(?,?,?,?)", (HASH, "b", "http://b/blob", now))
            distributed.advance(connection, now)
        reused = self.child(current)
        self.assertEqual(reused["state"], "complete")
        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT state FROM runs WHERE run_id=?", (queued["run_id"],),
            ).fetchone()[0], "cancelled")

        running = self.enqueue(rerun=True)
        running_child = self.child(running)
        with leader.connect(self.database) as connection:
            # Simulate an already leased tile before the recovery scheduler observes it.
            connection.execute("UPDATE runs SET state='running' WHERE run_id=?", (running_child["run_id"],))
            distributed.advance(connection, time.time())
        self.assertEqual(self.child(running)["run_id"], running_child["run_id"])

    def test_from_scratch_does_not_import(self) -> None:
        old = self.enqueue()
        self.complete_old_tile(old)
        fresh = self.enqueue(from_scratch=True)
        self.assertEqual(self.child(fresh)["state"], "queued")


if __name__ == "__main__":
    unittest.main()
