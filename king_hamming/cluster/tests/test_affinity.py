#!/usr/bin/env python3
"""Check soft row affinity: a node prefers tiles whose neighbours it holds, but only as a bounded tie-break."""

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
from dp_solver import distributed

PACKET = "a" * 64
SPECIFICATION = {"program": "dp_distributed", "arguments": {"p": 3, "r": 7, "tile_side": 32, "threads": 1}}


class AffinityTests(unittest.TestCase):
    """Tile (0,0) is finished by node a and stored on a and b; tiles (0,1) and (1,0) are then ready."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800, lease_seconds=300)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name in ("a", "b", "c"):
            self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})
        self.parent = self.handler.dispatch_post("/v1/enqueue", {"specification": SPECIFICATION})["run_id"]
        now = time.time()
        with leader.connect(self.database) as connection:
            child = self.child(connection, 0, 0)
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,9)", (PACKET, now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=?,finished=?,node_name='a' WHERE run_id=?",
                               (PACKET, now, child))
            for name in ("a", "b"):
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (PACKET, name, f"http://{name}/packet", now))
            distributed.advance(connection, now)
            # The plain queue order would take the older tile (1,0) first.
            connection.execute("UPDATE runs SET created=? WHERE run_id=?", (now - 60, self.child(connection, 1, 0)))
        self.right, self.below = self.coordinates(0, 1), self.coordinates(1, 0)

    def child(self, connection, row: int, column: int) -> str:
        return connection.execute("SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=? AND column=?",
                                  (self.parent, row, column)).fetchone()[0]

    def coordinates(self, row: int, column: int) -> str:
        with leader.connect(self.database) as connection:
            return self.child(connection, row, column)

    def lease(self, node: str) -> str:
        """Lease as node, then return the run to the queue so the next question starts clean."""

        job = self.handler.dispatch_post("/v1/lease", {"node_name": node})["job"]
        self.assertIsNotNone(job)
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='queued',node_name=NULL,lease_token=NULL WHERE run_id=?",
                               (job["run_id"],))
        return job["run_id"]

    def test_producer_of_the_left_neighbour_takes_the_next_tile_in_its_row(self) -> None:
        self.assertEqual(self.lease("a"), self.right)

    def test_a_node_that_only_stores_the_neighbour_is_preferred_over_one_that_does_not(self) -> None:
        self.assertEqual(self.lease("b"), self.right)

    def test_node_with_no_data_takes_the_oldest_tile(self) -> None:
        self.assertEqual(self.lease("c"), self.below)

    def test_priority_outranks_affinity(self) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET priority=5 WHERE run_id=?", (self.below,))
        self.assertEqual(self.lease("a"), self.below)

    def test_a_tile_that_waited_too_long_no_longer_waits_for_its_preferred_node(self) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET created=? WHERE run_id=?",
                               (time.time() - distributed.AFFINITY_MAX_WAIT_SECONDS - 1, self.below))
        self.assertEqual(self.lease("a"), self.below)
        with mock.patch.dict(os.environ, {"KH_ROW_AFFINITY_WAIT": "30"}):
            with leader.connect(self.database) as connection:
                connection.execute("UPDATE runs SET created=? WHERE run_id=?", (time.time() - 45, self.below))
            self.assertEqual(self.lease("a"), self.below)

    def test_kill_switch_restores_the_plain_order(self) -> None:
        with mock.patch.dict(os.environ, {"KH_ROW_AFFINITY": "0"}):
            self.assertEqual(self.lease("a"), self.below)

    def test_scores(self) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            items = [(run, {"program": "dp_tile", "arguments": {"parent_run_id": self.parent, "row": row, "column": column}}, now)
                     for run, (row, column) in ((self.right, (0, 1)), (self.below, (1, 0)))]
            items.append(("other", {"program": "demo", "arguments": {}}, now))
            self.assertEqual(distributed.locality_scores(connection, "a", items, now), {self.right: 3, self.below: 2})
            self.assertEqual(distributed.locality_scores(connection, "b", items, now), {self.right: 2, self.below: 1})
            self.assertEqual(distributed.locality_scores(connection, "c", items, now), {})


# Empty invocation is descriptive and starts nothing.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test soft DP row affinity.\nExample: python3 tests/test_affinity.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
