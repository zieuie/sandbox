#!/usr/bin/env python3
"""Test snapshot retirement, shared-object safety and publication/cleanup races."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent
import leader
import retention
from blob_store import blob_path, storage_transaction
from checkpoints import capture_checkpoint
from common import calculation_id


# Drive retention through production HTTP transaction handlers without household machines.
class RetentionTests(unittest.TestCase):
    """Keep fallbacks, live restores, shared content and attempt history safe during cleanup."""

    def setUp(self) -> None:
        """Create a private leader, two registered holders and one live demonstration run."""

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "leader.sqlite"
        leader.initialize(self.database, 1800, lease_seconds=300)
        self.handler = object.__new__(leader.make_handler(self.database))

        for name in ("a", "b"):
            self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})

        self.specification = {"program": "demo", "arguments": {"steps": 20}}
        self.handler.dispatch_post("/v1/enqueue", {"specification": self.specification})
        self.job = self.handler.dispatch_post("/v1/lease", {"node_name": "a"})["job"]
        self.work = self.root / "work"
        self.work.mkdir()
        self.storage = self.root / "blobs"
        self.manifests = []

    def snapshot(self, cursor: int, copies: int = 2) -> dict:
        """Capture and index cursor's real CAS files, acknowledging copies complete holders."""

        (self.work / "solver.checkpoint.json").write_text(json.dumps({"next_step": cursor + 1}))
        manifest = capture_checkpoint(self.specification, self.job["run_id"], self.work, self.storage, cursor)
        self.handler.dispatch_post("/v1/checkpoint", {**self.job, "manifest": manifest, "manifest_hash": calculation_id(manifest)})

        if copies == 2:
            self.handler.dispatch_post("/v1/checkpoint-replica", {"node_name": "b", "manifest_hash": calculation_id(manifest)})

        self.manifests.append(manifest)
        return manifest

    def plan(self) -> dict:
        """Return a garbage plan for holder a using current leader time."""

        return self.handler.dispatch_post("/v1/gc-plan", {"node_name": "a"})

    def test_requires_three_live_replicated_successors(self) -> None:
        """A pending snapshot or unreachable replica cannot justify deleting a fallback."""

        for cursor in range(1, 4):
            self.snapshot(cursor)

        self.snapshot(4, copies=1)
        self.assertEqual(self.plan()["retired"], 0)
        self.handler.dispatch_post("/v1/checkpoint-replica", {"node_name": "b", "manifest_hash": calculation_id(self.manifests[-1])})

        with leader.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=0 WHERE node_name='b'")

        self.assertEqual(self.plan()["retired"], 0)
        self.handler.dispatch_post("/v1/heartbeat", {"node_name": "b"})
        self.assertEqual(self.plan()["retired"], 1)

        with leader.connect(self.database) as connection:
            rows = list(connection.execute("SELECT cursor,retired_at FROM checkpoints ORDER BY cursor"))
            self.assertIsNotNone(rows[0]["retired_at"])
            self.assertTrue(all(row["retired_at"] is None for row in rows[1:]))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM lease_history").fetchone()[0], 1)

        with self.assertRaises(ValueError):
            self.handler.dispatch_post("/v1/checkpoint-replica", {"node_name": "b", "manifest_hash": calculation_id(self.manifests[0])})

    def test_active_restore_pin_survives_retirement(self) -> None:
        """A selected old snapshot remains available until its restore is acknowledged."""

        for cursor in range(1, 7):
            self.snapshot(cursor)

        excluded = [calculation_id(manifest) for manifest in self.manifests[1:]]
        selected = self.handler.dispatch_post("/v1/recovery", {**self.job, "exclude": excluded})["checkpoint"]
        oldest = selected["manifest_hash"]
        plan = self.plan()
        self.assertNotIn(oldest, plan["blob_hashes"])

        with leader.connect(self.database) as connection:
            self.assertIsNone(connection.execute("SELECT retired_at FROM checkpoints WHERE manifest_hash=?", (oldest,)).fetchone()[0])

        self.handler.dispatch_post("/v1/restored", {**self.job, "manifest_hash": oldest})
        self.assertIn(oldest, self.plan()["blob_hashes"])

    def test_shared_blobs_and_artifacts_are_not_deleted(self) -> None:
        """Retiring one run cannot delete bytes referenced by another run or final output."""

        for cursor in range(1, 7):
            self.snapshot(cursor)

        protected_artifact = self.manifests[0]["files"][0]["sha256"]
        with leader.connect(self.database) as connection:
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,?)",
                               (protected_artifact, time.time(), 1))

        self.handler.dispatch_post("/v1/enqueue", {"specification": self.specification, "rerun": True})
        other = self.handler.dispatch_post("/v1/lease", {"node_name": "b"})["job"]
        (self.work / "solver.checkpoint.json").write_text(json.dumps({"next_step": 3}))
        shared = capture_checkpoint(self.specification, other["run_id"], self.work, self.storage, 2)
        self.handler.dispatch_post("/v1/checkpoint", {**other, "manifest": shared, "manifest_hash": calculation_id(shared)})
        plan = self.plan()
        self.assertNotIn(protected_artifact, plan["blob_hashes"])
        self.assertNotIn(shared["files"][0]["sha256"], plan["blob_hashes"])
        self.assertIn(calculation_id(self.manifests[1]), plan["blob_hashes"])

    def test_worker_collection_waits_for_publication_and_retries_ack(self) -> None:
        """Storage locking excludes an in-flight writer; failed acknowledgment is idempotent."""

        for cursor in range(1, 7):
            self.snapshot(cursor)

        removed = []
        errors = []
        started = threading.Event()

        def request(base, route, value):
            """Route collector messages through the private leader's transactions."""

            return self.handler.dispatch_post(route, value)

        def collect():
            """Collect retired blobs while recording any thread failure for the test."""

            started.set()
            try:
                with patch.object(agent, "request_json", side_effect=request):
                    removed.append(agent.collect_garbage("unused", {"node_name": "a", "session_id": ""}, self.storage))
            except Exception as error:
                errors.append(error)

        with storage_transaction(self.storage):
            worker = threading.Thread(target=collect)
            worker.start()
            self.assertTrue(started.wait(timeout=2))
            time.sleep(0.08)
            self.assertTrue(worker.is_alive())
            self.assertTrue(blob_path(self.storage, calculation_id(self.manifests[0])).exists())

        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertGreater(removed[0], 0)
        self.assertFalse(blob_path(self.storage, calculation_id(self.manifests[0])).exists())
        self.assertTrue(blob_path(self.storage, calculation_id(self.manifests[-1])).exists())
        self.assertEqual(self.plan()["blob_hashes"], [])

        # Missing files after an interrupted acknowledgment remain safe to acknowledge again.
        for cursor in range(7, 9):
            self.snapshot(cursor)

        def interrupted(base, route, value):
            """Drop only the collector's final acknowledgment, after its durable deletions."""

            if route == "/v1/gc-done":
                raise OSError("lost acknowledgment")
            return request(base, route, value)

        with patch.object(agent, "request_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                agent.collect_garbage("unused", {"node_name": "a", "session_id": ""}, self.storage)

        with patch.object(agent, "request_json", side_effect=request):
            self.assertEqual(agent.collect_garbage("unused", {"node_name": "a", "session_id": ""}, self.storage), 0)
        self.assertEqual(self.plan()["blob_hashes"], [])

    def test_pin_expiration_and_retention_limit_validation(self) -> None:
        """Expired leases release pins; unsafe retention settings are rejected."""

        for cursor in range(1, 5):
            self.snapshot(cursor)

        self.handler.dispatch_post("/v1/recovery", {**self.job, "exclude": [calculation_id(m) for m in self.manifests[1:]]})

        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET lease_expires=0 WHERE run_id=?", (self.job["run_id"],))

        self.assertEqual(self.plan()["retired"], 1)

        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM checkpoint_pins").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                retention.initialize(connection, 1)


# No-argument execution is descriptive and never performs computation or SSH.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test safe snapshot retention and garbage collection.\nExample: python3 tests/test_retention.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
