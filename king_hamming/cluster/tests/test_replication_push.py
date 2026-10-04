#!/usr/bin/env python3
"""Copies are pushed to a chosen node when one lands, instead of waiting for agents to scan."""

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
import replication


class PushedCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.addCleanup(replication._idle_until.clear)
        self.addCleanup(replication._candidates.clear)
        replication._candidates.clear()
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name, private in (("a", True), ("b", True), ("c", True), ("wifi", False)):
            payload = {"node_name": name, "address": f"http://192.168.4.{name}:9000"}
            if private:
                payload.update(private_address=f"http://10.203.0.{name}:9000", private_group="switch")
            self.handler.dispatch_post("/v1/register", payload)
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET storage_free_bytes=?", (100 * 2**30,))
            self.sessions = dict(connection.execute("SELECT node_name,session_id FROM nodes"))
        self.digest = "b" * 64

    def produce(self, node: str = "a") -> None:
        """Record a completed artifact on node the way /v1/complete does."""
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,?,?,?)",
                               (self.digest, 3, now, 100))
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                               (self.digest, node, f"http://192.168.4.{node}:9000/blobs/{self.digest}", now))
            leader.push_copy(connection, self.digest, now, 60.0)

    def reserved(self) -> list[str]:
        with leader.connect(self.database) as connection:
            return [row[0] for row in connection.execute(
                "SELECT node_name FROM replica_transfers WHERE artifact_hash=?", (self.digest,))]

    def poll(self, node: str) -> dict | None:
        return self.handler.dispatch_post("/v1/replication",
                                          {"node_name": node, "session_id": self.sessions[node]})["replication"]

    def test_completion_reserves_the_second_copy_on_the_producers_lan(self) -> None:
        self.produce("a")
        self.assertEqual(len(self.reserved()), 1)
        self.assertIn(self.reserved()[0], {"b", "c"})

    def test_target_gets_its_copy_without_a_scan_and_the_third_follows(self) -> None:
        self.produce("a")
        target = self.reserved()[0]
        with mock.patch.object(replication, "select_candidate", side_effect=AssertionError("scanned")):
            task = self.poll(target)
        self.assertEqual(task["artifact_hash"], self.digest)
        self.assertIn("10.203.0.a", task["location"])
        self.handler.dispatch_post("/v1/replica", {
            "node_name": target, "session_id": self.sessions[target], "artifact_hash": self.digest,
            "location": f"http://192.168.4.{target}:9000/blobs/{self.digest}",
            "transfer_token": task["transfer_token"]})
        third = self.reserved()
        self.assertEqual(len(third), 1)
        self.assertNotIn(third[0], {"a", target})
        self.assertEqual(third[0], ({"b", "c"} - {target}).pop())

    def test_no_copy_is_pushed_once_enough_exist(self) -> None:
        self.produce("a")
        now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replica_transfers")
            for name in ("b", "c"):
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (self.digest, name, "http://x/blobs/" + self.digest, now))
            leader.push_copy(connection, self.digest, now, 60.0)
        self.assertEqual(self.reserved(), [])

    def test_a_paused_or_full_node_is_never_chosen(self) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO node_dispatch_pauses(node_name) VALUES('b')")
            connection.execute("UPDATE nodes SET storage_free_bytes=0 WHERE node_name='c'")
        self.produce("a")
        self.assertEqual(self.reserved(), ["wifi"])

    def test_an_empty_scan_holds_the_next_one(self) -> None:
        with mock.patch.object(replication, "select_candidate", return_value=None) as scan:
            self.assertIsNone(self.poll("b"))
            self.assertIsNone(self.poll("b"))
            self.assertEqual(scan.call_count, 1)
            replication._idle_until["b"] = time.time() - 1  # the hold has run out
            self.assertIsNone(self.poll("b"))
            self.assertEqual(scan.call_count, 2)

    def test_empty_revalidation_poll_needs_no_writer_lock(self) -> None:
        blocker = leader.connect(self.database)
        blocker.execute("BEGIN IMMEDIATE")       # a writer holds the lock for the whole poll
        try:
            with mock.patch.object(leader, "writer_session", side_effect=AssertionError("took the writer lock")):
                reply = self.handler.dispatch_post(
                    "/v1/revalidation-batch", {"node_name": "b", "session_id": self.sessions["b"]})
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(reply["records"], [])

    def test_pending_revalidation_still_reaches_the_writer_path(self) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,5)",
                               (self.digest, time.time()))
            connection.execute("INSERT INTO node_revalidation(node_name,kind,digest,created) VALUES('b','artifact',?,?)",
                               (self.digest, time.time()))
        reply = self.handler.dispatch_post("/v1/revalidation-batch",
                                           {"node_name": "b", "session_id": self.sessions["b"]})
        self.assertEqual([item["digest"] for item in reply["records"]], [self.digest])

    def test_one_scan_serves_every_node_for_the_cache_period(self) -> None:
        self.produce("a")
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replica_transfers")
        with mock.patch.object(replication, "scan_candidates", wraps=replication.scan_candidates) as scan:
            for name in ("b", "c", "wifi"):
                replication._idle_until.clear()
                self.poll(name)
            self.assertEqual(scan.call_count, 1)
            with leader.connect(self.database) as connection:
                connection.execute("DELETE FROM replica_transfers")   # no pending assignment to return
            replication._candidates.clear()
            replication._idle_until.clear()
            self.poll("b")
            self.assertEqual(scan.call_count, 2)

    def test_listed_artifact_is_skipped_once_it_has_enough_copies_or_a_transfer(self) -> None:
        self.produce("a")
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replica_transfers")
            now = time.time()
            with mock.patch.object(replication, "TRANSFER_SECONDS", 90.0):
                connection.execute(
                    "INSERT INTO replica_transfers(artifact_hash,node_name,session_id,token,created,expires) "
                    "VALUES(?,?,?,?,?,?)", (self.digest, "c", self.sessions["c"], "t" * 36, now, now + 90))
        replication._candidates.clear()
        self.assertIsNone(self.poll("b"), "someone is already copying it")
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replica_transfers")
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                               (self.digest, "c", "http://x/" + self.digest, time.time()))
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                               (self.digest, "wifi", "http://y/" + self.digest, time.time()))
        replication._idle_until.clear()
        self.assertIsNone(self.poll("b"), "three copies exist")

    def test_urgent_single_copy_artifact_of_a_waiting_root_is_listed_first(self) -> None:
        # Older background artifact with one copy, and a newer single-copy tile of a waiting root.
        now = time.time()
        old, tile = "d" * 64, "e" * 64
        spec = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7, "threads": 1}}
        root = self.handler.dispatch_post("/v1/enqueue", {"specification": spec})["run_id"]
        with leader.connect(self.database) as connection:
            child = connection.execute("SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? "
                                       "AND child_run_id IS NOT NULL LIMIT 1", (root,)).fetchone()[0]
            for digest, created in ((old, now - 1000), (tile, now)):
                connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,5)",
                                   (digest, created))
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (digest, "a", "http://a/" + digest, now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=? WHERE run_id=?", (tile, child))
            listed = replication.scan_candidates(connection, now, 60.0, 600.0)
        self.assertEqual(listed[0]["artifact_hash"], tile)
        self.assertIn(old, [entry["artifact_hash"] for entry in listed])


if __name__ == "__main__":
    unittest.main()
