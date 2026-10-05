#!/usr/bin/env python3
"""A fleet-wide restart must not make finished tiles look lost, and no pass may clear them in bulk."""

from __future__ import annotations

import json
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
from dp_solver import distributed

SPECIFICATION = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 2, "threads": 1}}
NODES = ("a", "b", "c")


class TileSafeguardTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        self.sessions = {}
        for name in NODES:
            self.register(name)
        self.root = self.handler.dispatch_post("/v1/enqueue", {"specification": SPECIFICATION})["run_id"]
        self.now = time.time() + 3600
        with leader.connect(self.database) as connection:
            self.cells = [(row[0], row[1]) for row in connection.execute(
                "SELECT row,column FROM distributed_tiles WHERE parent_run_id=? ORDER BY row,column", (self.root,))]
            self.finish_all(connection)

    def register(self, name: str) -> None:
        self.sessions[name] = str(uuid.uuid4())
        self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042",
                                                    "session_id": self.sessions[name]})

    def finish_all(self, connection) -> None:
        """Every tile finished long ago, with three copies."""
        for index, (row, column) in enumerate(self.cells):
            digest = f"{index:064x}"
            run = str(uuid.uuid4())
            specification = {"program": "dp_tile", "arguments": {"parent_run_id": self.root, "row": row, "column": column}}
            connection.execute(
                "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,started,"
                "finished,estimated_seconds,parent_run_id,progress_done,progress_total,progress_checkpoint_done,"
                "progress_phase,progress_units,progress_message,artifact_hash,artifact_location) "
                "VALUES(?,?,?,'complete',0,0,?,?,?,1,?,1,1,1,'complete','cells','',?,?)",
                (run, run, json.dumps(specification), self.now - 5000, self.now - 5000, self.now - 4000,
                 self.root, digest, "http://a/blob"))
            connection.execute("UPDATE distributed_tiles SET child_run_id=? WHERE parent_run_id=? AND row=? AND column=?",
                               (run, self.root, row, column))
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,9)",
                               (digest, self.now - 5000))
            for name in NODES:
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (digest, name, f"http://{name}/blob", self.now - 4000))

    def referenced(self, connection) -> int:
        return connection.execute("SELECT COUNT(*) FROM distributed_tiles WHERE parent_run_id=? "
                                  "AND child_run_id IS NOT NULL", (self.root,)).fetchone()[0]

    def beat(self, connection, when: float, names=NODES) -> None:
        for name in names:
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name=?", (when, name))

    def test_restarting_every_agent_clears_nothing(self) -> None:
        with leader.connect(self.database) as connection:
            self.assertGreater(len(self.cells), 100)
            before = self.referenced(connection)
        from unittest.mock import patch
        with patch.object(leader.time, "time", return_value=self.now):
            for name in NODES:          # a new session each: replica rows unverified, revalidation queued
                self.register(name)
        with leader.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM replicas WHERE verified=1").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM replicas").fetchone()[0],
                             len(self.cells) * len(NODES), "a restart keeps every claim")
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM node_revalidation").fetchone()[0], 100)
            self.beat(connection, self.now)
            distributed._last_refresh.clear()
            distributed.advance(connection, self.now + 5)
            self.assertEqual(self.referenced(connection), before, "no finished tile may be cleared")
            self.assertIsNone(connection.execute(
                "SELECT value FROM settings WHERE key=?", (f"tile_clear_hold:{self.root}",)).fetchone())

    def test_a_pass_that_would_clear_many_tiles_clears_none_and_says_so(self) -> None:
        with leader.connect(self.database) as connection:
            before = self.referenced(connection)
            # Every copy really gone: no replica rows, nothing awaiting revalidation, nodes alive.
            connection.execute("DELETE FROM replicas")
            self.beat(connection, self.now)
            distributed._last_refresh.clear()
            distributed.advance(connection, self.now + 5)
            self.assertEqual(self.referenced(connection), before, "the breaker holds the whole pass")
            hold = json.loads(connection.execute("SELECT value FROM settings WHERE key=?",
                                                 (f"tile_clear_hold:{self.root}",)).fetchone()[0])
            self.assertEqual((hold["would_clear"], hold["finished"]), (before, before))
            # An operator who knows the copies are gone raises the limit; then it recomputes.
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('tile_clear_max_fraction','1')")
            distributed._last_refresh.clear()
            distributed.advance(connection, self.now + 20)
            self.assertLess(self.referenced(connection), before)
            self.assertIsNone(connection.execute(
                "SELECT value FROM settings WHERE key=?", (f"tile_clear_hold:{self.root}",)).fetchone())

    def test_a_few_lost_tiles_are_still_recomputed(self) -> None:
        with leader.connect(self.database) as connection:
            before = self.referenced(connection)
            gone = [r[0] for r in connection.execute(
                "SELECT artifact_hash FROM artifacts ORDER BY artifact_hash LIMIT ?", (distributed.TILE_CLEAR_MIN_TILES - 1,))]
            for digest in gone:
                connection.execute("DELETE FROM replicas WHERE artifact_hash=?", (digest,))
            self.beat(connection, self.now)
            distributed._last_refresh.clear()
            distributed.advance(connection, self.now + 5)
            self.assertLess(self.referenced(connection), before)
            self.assertIsNone(connection.execute(
                "SELECT value FROM settings WHERE key=?", (f"tile_clear_hold:{self.root}",)).fetchone())

    def test_band_copies_of_retired_packets_are_swept_once_their_field_is_done(self) -> None:
        with leader.connect(self.database) as connection:
            packets = [row[0] for row in connection.execute("SELECT artifact_hash FROM artifacts ORDER BY artifact_hash LIMIT 3")]
            for index, packet in enumerate(packets):
                band = f"{index + 1:x}" * 64
                connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,1,9)", (band,))
                connection.execute("INSERT INTO tile_bands(packet_hash,kind,band_hash) VALUES(?,'right',?)", (packet, band))
                connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,'a','x',1)", (band,))
            # The first two packets were retired (no copies left); the third still has its copies.
            connection.execute("DELETE FROM replicas WHERE artifact_hash IN (?,?)", packets[:2])
            self.assertEqual(distributed.retire_orphan_bands(connection, self.now), 0, "the field is still active")
            connection.execute("UPDATE runs SET state='complete' WHERE run_id=?", (self.root,))
            self.assertEqual(distributed.retire_orphan_bands(connection, self.now), 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_trim WHERE reason='orphaned band'").fetchone()[0], 2)
            self.assertEqual(distributed.retire_orphan_bands(connection, self.now), 0)

    def test_the_upgrade_check_sees_lost_tiles_and_holds(self) -> None:
        from dp_solver import launch_dp
        before = launch_dp.tile_progress(self.database)
        self.assertEqual([entry["finished"] for entry in before.values()], [len(self.cells)])
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE distributed_tiles SET child_run_id=NULL WHERE parent_run_id=? AND row=0", (self.root,))
            connection.execute("INSERT INTO settings(key,value) VALUES(?,?)", (f"tile_clear_hold:{self.root}", json.dumps(
                {"time": 1, "would_clear": 7, "finished": 9, "limit": 50})))
        after = launch_dp.tile_progress(self.database)
        warnings = launch_dp.tile_progress_warnings(before, after)
        self.assertEqual(len(warnings), 1, "13 of 169 is within the 1% / 50-tile tolerance; only the hold is reported")
        self.assertIn("refusing to clear 7", warnings[0])
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE distributed_tiles SET child_run_id=NULL WHERE parent_run_id=?", (self.root,))
        warnings = launch_dp.tile_progress_warnings(before, launch_dp.tile_progress(self.database))
        self.assertTrue(any("finished tiles are no longer counted" in warning for warning in warnings))
        self.assertEqual(launch_dp.tile_progress(Path("/nonexistent/leader.sqlite")), {})


if __name__ == "__main__":
    unittest.main()
