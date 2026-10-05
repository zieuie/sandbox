#!/usr/bin/env python3
"""A restarted agent's copies stay recorded, unverified, until it re-checks its disk."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import leader
import recovery
import replication
import retention
import topology

NODES = ("a", "b", "c")
DIGEST = "ab" * 32
LEASE = 1800


class UnverifiedCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, LEASE)
        self.handler = object.__new__(leader.make_handler(self.database))
        self.sessions: dict[str, str] = {}
        for name in NODES:
            self.register(name)
        with leader.connect(self.database) as connection:
            old = time.time() - 100000
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,9)",
                               (DIGEST, old))
            for name in NODES:
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (DIGEST, name, f"http://{name}:8042/blobs/{DIGEST}", old))

    def register(self, name: str) -> None:
        self.sessions[name] = str(uuid.uuid4())
        self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042",
                                                    "session_id": self.sessions[name]})

    def claims(self, connection) -> dict[str, int]:
        return dict(connection.execute("SELECT node_name,verified FROM replicas WHERE artifact_hash=?", (DIGEST,)))

    def report(self, name: str, valid: bool) -> None:
        self.handler.dispatch_post("/v1/revalidation-batch-done", {
            "node_name": name, "session_id": self.sessions[name],
            "records": [{"kind": "artifact", "digest": DIGEST, "valid": valid}]})

    def test_a_restart_keeps_the_claim_but_never_reads_from_it(self) -> None:
        self.register("a")
        now = time.time()
        with leader.connect(self.database) as connection:
            self.assertEqual(self.claims(connection), {"a": 0, "b": 1, "c": 1})
            locations = topology.locations(connection, DIGEST, None, now - LEASE)
            self.assertEqual(len(locations), 2)
            self.assertFalse(any("//a:" in location for location in locations), "never a source")
            # Three recorded copies meet the target: nothing is re-copied while a re-checks.
            self.assertEqual(replication.scan_candidates(connection, now, LEASE, LEASE), [])
            # Nor does an unverified copy count as surplus or get trimmed.
            connection.execute("UPDATE artifacts SET target_replicas=1")
            self.assertEqual(retention.excess_plan(connection, "a", now, 10), [])
            self.assertEqual(len(retention.excess_plan(connection, "b", now, 10)), 1, "b and c: one is surplus")

    def test_a_proved_copy_is_verified_and_a_missing_one_is_forgotten(self) -> None:
        self.register("a")
        self.register("b")
        self.report("a", True)
        self.report("b", False)
        now = time.time()
        with leader.connect(self.database) as connection:
            self.assertEqual(self.claims(connection), {"a": 1, "c": 1})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM node_revalidation").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT storage_validation_mode FROM nodes WHERE node_name='b'")
                             .fetchone()[0], "verified")
            # Now short of its target, so it is copied again.
            self.assertEqual([entry["artifact_hash"] for entry in
                              replication.scan_candidates(connection, now, LEASE, LEASE)], [DIGEST])

    def test_the_one_at_a_time_check_forgets_an_unproved_copy(self) -> None:
        self.register("a")
        self.handler.dispatch_post("/v1/revalidate-done", {"node_name": "a", "session_id": self.sessions["a"],
                                                           "kind": "artifact", "digest": DIGEST})
        with leader.connect(self.database) as connection:
            self.assertEqual(self.claims(connection), {"b": 1, "c": 1})

    def test_a_copy_queued_for_deletion_is_not_revived_by_its_revalidation(self) -> None:
        """The race found on 2026-10-04: retirement queued a restarted node's copy for deletion while
        its re-check was pending; the check then re-recorded it as verified, and the file was deleted."""
        other = "cd" * 32
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,1,9)",
                               (other,))
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,1)",
                               (other, "a", f"http://a:8042/blobs/{other}"))
        self.register("a")  # both copies on a now await a re-check
        with leader.connect(self.database) as connection:
            retention.queue_trim(connection, "a", DIGEST, "finished field", time.time())
        # The agent reports a batch fetched before the trim: the trimmed copy is skipped, the rest kept.
        self.handler.dispatch_post("/v1/revalidation-batch-done", {
            "node_name": "a", "session_id": self.sessions["a"],
            "records": [{"kind": "artifact", "digest": DIGEST, "valid": True},
                        {"kind": "artifact", "digest": other, "valid": True}]})
        with leader.connect(self.database) as connection:
            self.assertNotIn("a", self.claims(connection))
            self.assertEqual(connection.execute("SELECT verified FROM replicas WHERE node_name='a' AND artifact_hash=?",
                                                (other,)).fetchone()[0], 1)

    def test_a_confirmed_deletion_drops_the_nodes_claim(self) -> None:
        self.handler.dispatch_post("/v1/gc-done", {"node_name": "b", "session_id": self.sessions["b"],
                                                    "blob_hashes": [DIGEST]})
        with leader.connect(self.database) as connection:
            self.assertEqual(self.claims(connection), {"a": 1, "c": 1})

    def test_queue_entries_from_an_older_leader_become_unverified_claims(self) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replicas WHERE node_name='a'")
            connection.execute("INSERT INTO node_revalidation(node_name,kind,digest,created) VALUES('a','artifact',?,1)",
                               (DIGEST,))
            recovery.restore_unverified_claims(connection)
            self.assertEqual(self.claims(connection), {"a": 0, "b": 1, "c": 1})
            self.assertEqual(connection.execute("SELECT location FROM replicas WHERE node_name='a'").fetchone()[0],
                             f"http://a:8042/blobs/{DIGEST}")


if __name__ == "__main__":
    unittest.main()
