#!/usr/bin/env python3
"""Recover durable distributed DP tiles across otherwise independent attempts."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import leader
from dp_solver import distributed
from common import canonical_json


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

    def test_blocked_frontier_does_not_build_tile_descriptors(self) -> None:
        root = self.enqueue()
        with leader.connect(self.database) as connection:
            with patch.object(distributed, "tile", wraps=distributed.tile) as make_tile:
                distributed.advance(connection, time.time())
            self.assertEqual(make_tile.call_count, 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NULL",
                (root,)).fetchone()[0], 15)

    def test_tile_input_read_does_not_wait_for_writer_and_fences_expired_lease(self) -> None:
        root = self.enqueue()
        child = self.child(root)
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) "
                               "VALUES(?,2,?,9)", (HASH, now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=? WHERE run_id=?",
                               (HASH, child["run_id"]))
            for name in ("a", "b"):
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                                   "VALUES(?,?,?,?)", (HASH, name, f"http://{name}/blob", now))
            connection.execute("UPDATE runs SET state='running',node_name='a',lease_token='lease',"
                               "lease_expires=? WHERE run_id=?", (now + 60, root))
        blocker = sqlite3.connect(self.database)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE settings SET value=value WHERE key='campaign_state'")
            result = self.handler.dispatch_post("/v1/tile-input", {
                "run_id": root, "lease_token": "lease", "row": 0, "column": 0})
            self.assertEqual(result["records"][0]["sha256"], HASH)
        finally:
            blocker.rollback()
            blocker.close()
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET lease_expires=? WHERE run_id=?", (now - 1, root))
        with self.assertRaises(PermissionError):
            self.handler.dispatch_post("/v1/tile-input", {
                "run_id": root, "lease_token": "lease", "row": 0, "column": 0})

    def test_failed_parent_retries_reconstruction_without_new_tiles(self) -> None:
        root = self.enqueue()
        now = time.time()
        with leader.connect(self.database) as connection:
            parent = connection.execute("SELECT * FROM runs WHERE run_id=?", (root,)).fetchone()
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) "
                               "VALUES(?,2,?,9)", (HASH, now))
            for name in ("a", "b"):
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                                   "VALUES(?,?,?,?)", (HASH, name, f"http://{name}/blob", now))
            for row, column, child in connection.execute(
                    "SELECT row,column,child_run_id FROM distributed_tiles WHERE parent_run_id=?",
                    (root,)).fetchall():
                if child is None:
                    child = str(uuid.uuid4())
                    specification = distributed.child_specification(parent, row, column)
                    connection.execute(
                        "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,"
                        "created,parent_run_id,artifact_hash) VALUES(?,?,?,'complete',0,0,?,?,?)",
                        (child, child, canonical_json(specification).decode(), now, root, HASH))
                    connection.execute("UPDATE distributed_tiles SET child_run_id=? "
                                       "WHERE parent_run_id=? AND row=? AND column=?",
                                       (child, root, row, column))
                else:
                    connection.execute("UPDATE runs SET state='complete',artifact_hash=? WHERE run_id=?",
                                       (HASH, child))
            connection.execute("UPDATE runs SET state='failed',error='timed out',engine_failures=1 "
                               "WHERE run_id=?", (root,))
            count_before = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        response = self.handler.dispatch_post("/v1/run-command", {
            "run_id": root, "action": "retry-reconstruction"})
        self.assertEqual(response, {"run_id": root, "state": "queued", "retained_tiles": 16})
        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], count_before)
            state = connection.execute("SELECT state,engine_failures,progress_phase FROM runs WHERE run_id=?",
                                       (root,)).fetchone()
            self.assertEqual(tuple(state), ("queued", 0, "reconstructing"))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NOT NULL",
                (root,)).fetchone()[0], 16)

    def test_reconstruction_retry_refuses_incomplete_grid(self) -> None:
        root = self.enqueue()
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='failed',error='timed out' WHERE run_id=?", (root,))
        with self.assertRaisesRegex(ValueError, "every original tile"):
            self.handler.dispatch_post("/v1/run-command", {
                "run_id": root, "action": "retry-reconstruction"})
        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT state FROM runs WHERE run_id=?",
                                                (root,)).fetchone()[0], "failed")

    def test_bounded_scheduler_rotates_across_waiting_roots(self) -> None:
        first = self.enqueue()
        second = self.enqueue(rerun=True)
        with leader.connect(self.database) as connection:
            with patch.object(distributed, "_last_scan", {}), patch.object(
                    distributed, "reuse_tiles", wraps=distributed.reuse_tiles) as scans:
                distributed.advance(connection, time.time(), max_roots=1)
                distributed.advance(connection, time.time() + 1, max_roots=1)
            self.assertEqual({call.args[1]["run_id"] for call in scans.call_args_list},
                             {first, second})

    def test_large_roots_are_rescanned_at_a_bounded_rate(self) -> None:
        """A root above FULL_SCAN_TILES is skipped until its interval passes; small roots never are."""
        root = self.enqueue()
        now = time.time()
        with leader.connect(self.database) as connection:
            with patch.object(distributed, "FULL_SCAN_TILES", 4), \
                    patch.object(distributed, "_last_scan", {}), \
                    patch.object(distributed, "reuse_tiles", wraps=distributed.reuse_tiles) as scans:
                distributed.advance(connection, now)
                distributed.advance(connection, now + 1)
                self.assertEqual(scans.call_count, 1)
                distributed.advance(connection, now + 5)
                self.assertEqual(scans.call_count, 2)
                distributed.advance(connection, now - 60)
                self.assertEqual(scans.call_count, 3)
            with patch.object(distributed, "reuse_tiles", wraps=distributed.reuse_tiles) as scans:
                distributed.advance(connection, now)
                distributed.advance(connection, now)
                self.assertEqual(scans.call_count, 2)
        self.assertTrue(root)

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

    def test_failed_tile_retries_in_same_root_with_backoff(self) -> None:
        root = self.enqueue()
        first = self.child(root)
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='failed',error='connection reset' WHERE run_id=?",
                               (first["run_id"],))
            distributed.advance(connection, now)
            slot = connection.execute(
                "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone()
            self.assertIsNone(slot[0])
            self.assertEqual(connection.execute(
                "SELECT state FROM runs WHERE run_id=?", (root,)).fetchone()[0], "waiting")
            distributed.advance(connection, now + 29)
            self.assertIsNone(connection.execute(
                "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone()[0])
            distributed.advance(connection, now + 31)
            replacement = connection.execute(
                "SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                "WHERE t.parent_run_id=? AND t.row=0 AND t.column=0", (root,)).fetchone()
            self.assertEqual(replacement["state"], "queued")
            self.assertNotEqual(replacement["run_id"], first["run_id"])
            self.assertEqual(connection.execute(
                "SELECT failures FROM distributed_tile_retries WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone()[0], 1)

    def test_successful_retry_clears_its_coordinate_failure_streak(self) -> None:
        root = self.enqueue()
        now = time.time()
        with leader.connect(self.database) as connection:
            first = connection.execute(
                "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone()[0]
            connection.execute("UPDATE runs SET state='failed',error='transient' WHERE run_id=?", (first,))
            distributed.advance(connection, now)
            distributed.advance(connection, now + 31)
            second = connection.execute(
                "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone()[0]
            self.assertNotEqual(second, first)
            digest = "b" * 64
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) "
                               "VALUES(?,2,?,9)", (digest, now))
            for name in ("a", "b"):
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                                   "VALUES(?,?,?,?)", (digest, name, f"http://{name}/blob", now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=? WHERE run_id=?",
                               (digest, second))
            distributed.advance(connection, now + 32)
            self.assertIsNone(connection.execute(
                "SELECT failures FROM distributed_tile_retries WHERE parent_run_id=? AND row=0 AND column=0",
                (root,)).fetchone())
            self.assertEqual(connection.execute(
                "SELECT state FROM runs WHERE run_id=?", (root,)).fetchone()[0], "waiting")

    def test_repeated_failure_of_one_coordinate_is_bounded(self) -> None:
        root = self.enqueue()
        now = time.time()
        with leader.connect(self.database) as connection:
            for attempt in range(4):
                child = connection.execute(
                    "SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                    "WHERE t.parent_run_id=? AND t.row=0 AND t.column=0", (root,)).fetchone()
                connection.execute("UPDATE runs SET state='failed',error='persistent failure' WHERE run_id=?",
                                   (child["run_id"],))
                distributed.advance(connection, now)
                state = connection.execute(
                    "SELECT state FROM runs WHERE run_id=?", (root,)).fetchone()[0]
                if attempt < 3:
                    self.assertEqual(state, "waiting")
                    now += min(300, 30 * 2**attempt) + 1
                    distributed.advance(connection, now)
                else:
                    self.assertEqual(state, "failed")
            self.assertIn("failed repeatedly", connection.execute(
                "SELECT error FROM runs WHERE run_id=?", (root,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
