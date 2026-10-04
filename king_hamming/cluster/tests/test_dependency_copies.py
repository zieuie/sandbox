#!/usr/bin/env python3
"""Tiles may start from one live copy of their inputs; lost tiles are recomputed, not fatal."""

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
sys.path.insert(0, str(ROOT.parent))

import leader
from dp_solver import distributed

SPECIFICATION = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7, "threads": 1}}
HASH = "c" * 64


class DependencyCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name in ("a", "b"):
            self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})
        self.root = self.handler.dispatch_post("/v1/enqueue", {"specification": SPECIFICATION})["run_id"]

    def tile(self, connection, row: int, column: int):
        return connection.execute(
            "SELECT r.* FROM distributed_tiles t LEFT JOIN runs r ON r.run_id=t.child_run_id "
            "WHERE t.parent_run_id=? AND t.row=? AND t.column=?", (self.root, row, column)).fetchone()

    def complete_corner(self, connection, now: float, finished: float | None = None) -> str:
        """Finish tile (0,0) on node a with one copy; return its run id."""
        run = self.tile(connection, 0, 0)["run_id"]
        connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,9)",
                           (HASH, now))
        connection.execute("UPDATE runs SET state='complete',artifact_hash=?,finished=?,node_name='a' WHERE run_id=?",
                           (HASH, finished or now, run))
        connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                           (HASH, "a", "http://a/blob", now))
        return run

    def test_one_live_copy_makes_successors_ready(self) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            self.complete_corner(connection, now)
            distributed.advance(connection, now)
            self.assertEqual(self.tile(connection, 0, 1)["state"], "queued")
            self.assertEqual(self.tile(connection, 1, 0)["state"], "queued")

    def test_setting_can_require_two_copies_again(self) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('dependency_replicas','2')")
            self.complete_corner(connection, now)
            distributed.advance(connection, now)
            self.assertIsNone(self.tile(connection, 0, 1)["run_id"])

    def test_tile_whose_copies_stay_unreachable_past_the_grace_is_recomputed(self) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            old = self.complete_corner(connection, now, finished=now - 700)
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='a'", (now - 100,))
            distributed.advance(connection, now)
            self.assertEqual(self.tile(connection, 0, 0)["run_id"], old, "a short absence keeps the tile")
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='a'", (now - 700,))
            distributed.advance(connection, now)
            again = self.tile(connection, 0, 0)
            self.assertNotEqual(again["run_id"], old)
            self.assertEqual(again["state"], "queued")
            self.assertEqual(connection.execute("SELECT state FROM runs WHERE run_id=?", (self.root,)).fetchone()[0],
                             "waiting")

    def test_failure_while_an_input_is_unreachable_does_not_spend_an_attempt(self) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            self.complete_corner(connection, now)
            distributed.advance(connection, now)
            successor = self.tile(connection, 0, 1)["run_id"]
            connection.execute("UPDATE runs SET state='failed',error='HTTP Error 400',finished=? WHERE run_id=?",
                               (now, successor))
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='a'", (now - 120,))
            distributed.advance(connection, now)
            self.assertIsNone(self.tile(connection, 0, 1)["run_id"])
            self.assertEqual(connection.execute(
                "SELECT failures FROM distributed_tile_retries WHERE parent_run_id=? AND row=0 AND column=1",
                (self.root,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
