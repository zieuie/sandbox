#!/usr/bin/env python3
"""Check disk reclamation: surplus copies, retired tiles of finished fields, and scratch directories."""

from __future__ import annotations

import os
from contextlib import contextmanager
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

import agent
import leader
import retention
from dp_solver import distributed
from test_integration import find_run, request_json, wait_until
from test_recovery import Cluster

SPECIFICATION = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7, "threads": 1}}
HOURS = 3600.0


def digest(character: str) -> str:
    return character * 64


class LeaderCase(unittest.TestCase):
    """A private leader with registered nodes; the plan and acknowledgment routes are called directly."""

    NODES = ("a", "b", "c", "d", "e")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name in self.NODES:
            self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})
        retention._last_trim_scan.clear()
        self.now = time.time()
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=?", (self.now,))
            # More free disk the later in the alphabet, all comfortably above the disk floor.
            for index, name in enumerate(self.NODES):
                connection.execute("UPDATE nodes SET storage_free_bytes=? WHERE node_name=?", ((index + 1) * 100 * 2**30, name))

    def db(self):
        return leader.connect(self.database)

    def artifact(self, connection, name: str, holders, target: int = 3, size: int = 1000, age: float = 2 * HOURS) -> str:
        value = name if len(name) == 64 else digest(name)
        connection.execute("INSERT OR IGNORE INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,?,?,?)",
                           (value, target, self.now - age, size))
        for node in holders:
            connection.execute("INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                               (value, node, f"http://{node}/blobs/{value}", self.now - age))
        return value

    def plan(self, node: str) -> list[str]:
        return self.handler.dispatch_post("/v1/gc-plan", {"node_name": node, "session_id": ""})["blob_hashes"]

    def done(self, node: str, hashes) -> None:
        self.handler.dispatch_post("/v1/gc-done", {"node_name": node, "session_id": "", "blob_hashes": list(hashes)})

    def holders(self, connection, value: str) -> set[str]:
        return {row[0] for row in connection.execute("SELECT node_name FROM replicas WHERE artifact_hash=?", (value,))}


class SurplusCopyTests(LeaderCase):
    def test_the_machines_with_least_free_disk_drop_surplus_copies_first(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "1", self.NODES)             # five copies, target three
        dropped = {node: self.plan(node) for node in self.NODES}
        self.assertEqual({node for node, hashes in dropped.items() if hashes}, {"a", "b"})
        self.assertEqual(dropped["a"], [value])
        with self.db() as connection:
            self.assertEqual(self.holders(connection, value), {"c", "d", "e"})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_trim").fetchone()[0], 2)
        # The agent deletes the blob and acknowledges; the queue empties and nothing more is asked.
        self.done("a", [value])
        self.assertEqual(self.plan("a"), [])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_trim WHERE node_name='a'").fetchone()[0], 0)

    def test_never_below_the_target_among_healthy_machines(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "2", self.NODES)
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name IN ('d','e')", (self.now - 600,))
        # Only a, b, c are healthy, which is exactly the target: nobody drops anything.
        self.assertEqual([self.plan(node) for node in self.NODES], [[]] * 5)
        with self.db() as connection:
            self.assertEqual(self.holders(connection, value), set(self.NODES))

    def test_stale_machines_are_not_asked_and_do_not_count(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "3", self.NODES)
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='a'", (self.now - 600,))
        # b, c, d, e are healthy: one surplus copy, held by the healthiest-disk-poor of them (b).
        self.assertEqual(self.plan("b"), [value])
        self.assertEqual([self.plan(node) for node in ("c", "d", "e")], [[]] * 3)

    def test_a_young_copy_is_left_alone(self) -> None:
        with self.db() as connection:
            self.artifact(connection, "4", self.NODES, age=60)
        self.assertEqual([self.plan(node) for node in self.NODES], [[]] * 5)

    def test_artifacts_at_or_below_target_are_untouched_and_targets_are_per_artifact(self) -> None:
        with self.db() as connection:
            three = self.artifact(connection, "5", "abc")
            two_of_two = self.artifact(connection, "6", "ab", target=2)
            under = self.artifact(connection, "7", "a")
            five = self.artifact(connection, "8", self.NODES, target=5)
        self.assertEqual([self.plan(node) for node in self.NODES], [[]] * 5)
        with self.db() as connection:
            for value, expected in ((three, set("abc")), (two_of_two, set("ab")), (under, {"a"}), (five, set(self.NODES))):
                self.assertEqual(self.holders(connection, value), expected)

    def test_a_trim_is_dropped_from_the_index_at_once_and_survives_a_lost_acknowledgment(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "9", self.NODES)
        self.assertEqual(self.plan("a"), [value])
        # No acknowledgment arrives (agent crashed): the same deletion is offered again.
        self.assertEqual(self.plan("a"), [value])

    def test_a_blob_that_is_also_a_retained_checkpoint_member_is_not_deleted(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "a", self.NODES)
        with mock.patch.object(retention, "members", lambda row: {value}):
            with self.db() as connection:
                connection.execute(
                    "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created) "
                    "VALUES('r','c','{}','queued',0,0,0)")
                connection.execute(
                    "INSERT INTO checkpoints(manifest_hash,run_id,cursor,done,manifest,created) VALUES('m','r',1,1,'{}',0)")
            self.assertEqual(self.plan("a"), [])
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifact_trim WHERE node_name='a'").fetchone()[0], 0)

    def test_only_a_registered_session_can_ask(self) -> None:
        with self.assertRaises(PermissionError):
            self.handler.dispatch_post("/v1/gc-plan", {"node_name": "a", "session_id": "someone else"})


class ReplicationGraceTests(LeaderCase):
    """A node that is silent for a few minutes keeps its copies; only a long absence is replaced."""

    def wanted(self, node: str):
        return self.handler.dispatch_post("/v1/replication", {"node_name": node, "session_id": ""})["replication"]

    def setUp(self) -> None:
        super().setUp()
        with self.db() as connection:
            self.value = self.artifact(connection, "b", "ab", target=2, age=HOURS)

    def silent(self, seconds: float) -> None:
        with self.db() as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='b'", (self.now - seconds,))

    def test_a_short_outage_does_not_trigger_a_new_copy(self) -> None:
        self.silent(120)           # past the 60 s lease window, well inside the grace period
        self.assertIsNone(self.wanted("c"))

    def test_a_long_outage_is_replaced(self) -> None:
        self.silent(retention.DEFAULT_REPLICA_GRACE_SECONDS + 60)
        task = self.wanted("c")
        self.assertEqual(task["artifact_hash"], self.value)
        self.assertEqual(task["locations"], [f"http://a/blobs/{self.value}"])  # copied from a live holder only

    def test_the_grace_period_is_a_setting(self) -> None:
        self.silent(120)
        with self.db() as connection:
            connection.execute("UPDATE settings SET value='30' WHERE key='replica_grace_seconds'")
        self.assertIsNotNone(self.wanted("c"))   # never shorter than the lease window, but 120 s exceeds 60 s

    def test_with_no_live_copy_nothing_can_be_replicated(self) -> None:
        self.silent(HOURS)
        with self.db() as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='a'", (self.now - HOURS,))
        self.assertIsNone(self.wanted("c"))


class ReplicationSchedulerTests(LeaderCase):
    """One fenced transfer at a time, with frontier-blocking second copies first."""

    def wanted(self, node: str):
        return self.handler.dispatch_post(
            "/v1/replication", {"node_name": node, "session_id": ""})["replication"]

    def test_exclusive_transfer_and_release(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "1", "a")
        task = self.wanted("b")
        self.assertEqual(task["artifact_hash"], value)
        self.assertIsNone(self.wanted("c"))
        self.handler.dispatch_post("/v1/replication-release", {
            "node_name": "b", "session_id": "", "artifact_hash": value,
            "transfer_token": task["transfer_token"]})
        self.assertEqual(self.wanted("c")["artifact_hash"], value)

    def test_fenced_ack_and_expiry(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "2", "a", target=2)
        task = self.wanted("b")
        with self.assertRaises(PermissionError):
            self.handler.dispatch_post("/v1/replica", {
                "node_name": "b", "session_id": "", "artifact_hash": value,
                "transfer_token": "0" * 36, "location": "http://b/blobs/" + value})
        self.handler.dispatch_post("/v1/replication-renew", {
            "node_name": "b", "session_id": "", "artifact_hash": value,
            "transfer_token": task["transfer_token"]})
        with self.db() as connection:
            connection.execute(
                "INSERT INTO artifact_trim(node_name,artifact_hash,reason,created) VALUES(?,?,?,?)",
                ("b", value, "obsolete claim", self.now))
        self.handler.dispatch_post("/v1/replica", {
            "node_name": "b", "session_id": "", "artifact_hash": value,
            "transfer_token": task["transfer_token"], "location": "http://b/blobs/" + value})
        with self.db() as connection:
            self.assertEqual(self.holders(connection, value), {"a", "b"})
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM replica_transfers").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM artifact_trim WHERE node_name='b' AND artifact_hash=?",
                (value,)).fetchone()[0], 0)

        with self.db() as connection:
            another = self.artifact(connection, "3", "a", target=2)
        old = self.wanted("b")
        with self.db() as connection:
            connection.execute("UPDATE replica_transfers SET expires=? WHERE artifact_hash=?",
                               (self.now - 1, another))
        new = self.wanted("c")
        self.assertEqual(new["artifact_hash"], another)
        with self.assertRaises(PermissionError):
            self.handler.dispatch_post("/v1/replica", {
                "node_name": "b", "session_id": "", "artifact_hash": another,
                "transfer_token": old["transfer_token"], "location": "http://b/blobs/" + another})

    def test_active_tile_second_copy_precedes_older_background_work(self) -> None:
        with self.db() as connection:
            older = self.artifact(connection, "4", "a", age=2 * HOURS)
            urgent = self.artifact(connection, "5", "a", age=HOURS)
            parent = "frontier-root"
            connection.execute(
                "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created) "
                "VALUES(?,?,?,'waiting',0,0,?)",
                (parent, parent, '{"program":"dp_distributed","arguments":{"p":5,"r":3}}', self.now))
            connection.execute(
                "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,parent_run_id,artifact_hash) "
                "VALUES(?,?,?,'complete',0,0,?,?,?)",
                ("frontier-tile", "frontier-tile", '{"program":"dp_tile","arguments":{}}',
                 self.now, parent, urgent))
        self.assertEqual(self.wanted("b")["artifact_hash"], urgent)
        self.assertEqual(self.wanted("c")["artifact_hash"], older)


class AgentReplicationTests(unittest.TestCase):
    """Artifact transfers renew their claim without blocking tile publication."""

    def test_download_renews_and_releases_without_storage_wide_lock(self) -> None:
        digest_value = digest("a")
        task = {"kind": "artifact", "artifact_hash": digest_value, "size": 100,
                "location": "http://source/blobs/" + digest_value,
                "locations": ["http://source/blobs/" + digest_value],
                "transfer_token": "1" * 36}
        routes = []
        locked = False

        @contextmanager
        def transaction(_root, check=None):
            nonlocal locked
            self.assertFalse(locked)
            if check is not None:
                check()
            locked = True
            try:
                yield
            finally:
                locked = False

        def request(_leader, route, _value):
            routes.append(route)
            if route == "/v1/revalidation-batch":
                return {"records": []}
            if route == "/v1/replication":
                return {"replication": task}
            return {"ok": True}

        def fetch(root, _digest, _size, _locations, check=None):
            self.assertFalse(locked)
            self.assertIsNotNone(check)
            check()
            path = agent.blob_path(root, _digest)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * _size)
            return path

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(agent, "request_json", side_effect=request), \
                mock.patch.object(agent, "fetch_blob", side_effect=fetch), \
                mock.patch.object(agent, "storage_transaction", side_effect=transaction):
            self.assertTrue(agent.replicate_once("http://leader", "b", Path(directory), "http://b"))
        self.assertEqual(routes, ["/v1/revalidation-batch", "/v1/replication",
                                  "/v1/replication-renew", "/v1/replica",
                                  "/v1/replication-release"])

    def test_failed_download_releases_assignment(self) -> None:
        digest_value = digest("b")
        task = {"kind": "artifact", "artifact_hash": digest_value, "size": 100,
                "location": "http://source/blobs/" + digest_value,
                "transfer_token": "2" * 36}
        routes = []

        def request(_leader, route, _value):
            routes.append(route)
            if route == "/v1/revalidation-batch":
                return {"records": []}
            if route == "/v1/replication":
                return {"replication": task}
            return {"ok": True}

        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(agent, "request_json", side_effect=request), \
                mock.patch.object(agent, "fetch_blob", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                agent.replicate_once("http://leader", "b", Path(directory), "http://b")
        self.assertEqual(routes[-1], "/v1/replication-release")


class DiskFloorTests(LeaderCase):
    """New work and new copies stop at the free-space floor; running work, reads and cleanup go on."""

    GIB = 2**30

    def setUp(self) -> None:
        super().setUp()
        self.handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo", "arguments": {"steps": 20}}})

    def set_free(self, node: str, free) -> None:
        with self.db() as connection:
            connection.execute("UPDATE nodes SET storage_free_bytes=? WHERE node_name=?", (free, node))

    def lease(self, node: str = "a") -> dict:
        return self.handler.dispatch_post("/v1/lease", {"node_name": node, "session_id": ""})

    def test_a_node_below_the_floor_gets_no_new_lease_and_is_told_why(self) -> None:
        self.set_free("a", 5 * self.GIB)
        answer = self.lease()
        self.assertIsNone(answer["job"])
        self.assertEqual(answer["reason"], "low disk")

    def test_exactly_full_is_blocked_and_at_the_floor_is_allowed(self) -> None:
        self.set_free("a", 0)
        self.assertIsNone(self.lease()["job"])
        self.set_free("a", 10 * self.GIB - 1)
        self.assertIsNone(self.lease()["job"])
        self.set_free("a", 10 * self.GIB)
        self.assertIsNotNone(self.lease()["job"])

    def test_other_nodes_are_unaffected(self) -> None:
        self.set_free("a", 1)
        self.assertIsNone(self.lease("a")["job"])
        self.assertIsNotNone(self.lease("b")["job"])

    def test_an_agent_that_never_reported_free_space_is_not_blocked(self) -> None:
        self.handler.dispatch_post("/v1/register", {"node_name": "f", "address": "http://f:8042"})
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT storage_free_bytes FROM nodes WHERE node_name='f'").fetchone()[0], -1)
        self.assertIsNotNone(self.lease("f")["job"])
        self.handler.dispatch_post("/v1/register", {"node_name": "g", "address": "http://g:8042", "storage_free_bytes": 0})
        with self.db() as connection:
            self.assertEqual(connection.execute("SELECT storage_free_bytes FROM nodes WHERE node_name='g'").fetchone()[0], 0)

    def test_the_floor_is_a_setting_and_zero_turns_it_off(self) -> None:
        self.set_free("a", 5 * self.GIB)
        with self.db() as connection:
            connection.execute("UPDATE settings SET value=? WHERE key='disk_floor_bytes'", (str(4 * self.GIB),))
        self.assertIsNotNone(self.lease()["job"])
        self.handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo", "arguments": {"steps": 21}}})
        self.set_free("b", 0)
        with self.db() as connection:
            connection.execute("UPDATE settings SET value='0' WHERE key='disk_floor_bytes'")
        self.assertIsNotNone(self.lease("b")["job"])

    def test_no_new_copies_are_sent_to_a_nearly_full_node_but_inventory_checks_continue(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "c", "ab", target=3)
        self.set_free("c", 2 * self.GIB)
        ask = lambda: self.handler.dispatch_post("/v1/replication", {"node_name": "c", "session_id": ""})["replication"]
        self.assertIsNone(ask())
        with self.db() as connection:                       # a stored-bytes check costs no space
            connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,10)", (digest("d"), self.now))
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)", (digest("d"), "c", "x", self.now))
            connection.execute("INSERT INTO node_revalidation(node_name,kind,digest,created) VALUES('c','artifact',?,?)", (digest("d"), self.now))
        self.assertEqual(ask()["kind"], "revalidate_artifact")
        with self.db() as connection:
            connection.execute("DELETE FROM node_revalidation")
        self.set_free("c", 50 * self.GIB)
        self.assertEqual(ask()["artifact_hash"], value)

    def test_the_idle_reason_names_the_cause(self) -> None:
        self.set_free("a", int(1.5 * self.GIB))
        with self.db() as connection:
            nodes = [dict(row, reserved_for=None) for row in connection.execute("SELECT * FROM nodes ORDER BY node_name")]
            leader.annotate_idle_reasons(connection, nodes, [], "running", 60.0, self.now)
        reasons = {node["node_name"]: node["idle_reason"] for node in nodes}
        self.assertEqual(reasons["a"], "low disk (1.5 GiB free)")
        self.assertNotIn("low disk", reasons["b"])

    def test_cleanup_still_reaches_a_full_node(self) -> None:
        with self.db() as connection:
            value = self.artifact(connection, "e", self.NODES)
        self.set_free("a", 0)
        self.assertEqual(self.plan("a"), [value])             # trimming is how it gets space back


class RetireFinishedTilesTests(LeaderCase):
    """Tiles go only when every root that could use them is finished and its result is safely stored."""

    def setUp(self) -> None:
        super().setUp()
        self.roots = {}

    def root(self, name: str, state: str, *, field=(5, 3), finished_ago: float | None = 24 * HOURS,
             result_copies: str = "ab", tiles=("t",), side: int = 7) -> str:
        """Insert a root of field with one packet (and one band) per entry of tiles, shared by name."""

        spec = {"program": "dp_distributed", "arguments": {"p": field[0], "r": field[1], "tile_side": side, "threads": 1}}
        import json
        specification = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        run_id = f"root-{name}"
        calculation = f"calc-{field[0]}-{field[1]}-{side}"
        with self.db() as connection:
            result = self.artifact(connection, digest_of(name, "r"), result_copies) if state == "complete" else None
            connection.execute(
                "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,finished,artifact_hash) "
                "VALUES(?,?,?,?,0,0,?,?,?)",
                (run_id, calculation, specification, state, self.now - 30 * HOURS,
                 None if finished_ago is None else self.now - finished_ago, result))
            for index, tile_name in enumerate(tiles):
                packet = self.packet(connection, tile_name)
                child = f"{run_id}-{index}"
                tile_spec = json.dumps({"program": "dp_tile", "arguments": {"p": field[0], "r": field[1], "row": index, "column": 0,
                                                                              "parent_run_id": run_id, "tile_side": side}}, sort_keys=True)
                connection.execute(
                    "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,parent_run_id,artifact_hash) "
                    "VALUES(?,?,?,'complete',0,0,?,?,?)", (child, f"{child}-calc", tile_spec, self.now - 30 * HOURS, run_id, packet))
        self.roots[name] = run_id
        return run_id

    def packet(self, connection, name: str) -> str:
        """A tile packet and its bottom band, each on three nodes; created once per name."""

        packet, band = digest_of(name, "p"), digest_of(name, "b")
        for value in (packet, band):
            connection.execute("INSERT OR IGNORE INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,1000)",
                               (value, self.now - 30 * HOURS))
            for node in "cde":
                connection.execute("INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (value, node, f"http://{node}/blobs/{value}", self.now - 30 * HOURS))
        connection.execute("INSERT OR REPLACE INTO tile_bands(packet_hash,kind,band_hash) VALUES(?,'bottom',?)", (packet, band))
        return packet

    def retire(self, **options) -> int:
        with self.db() as connection:
            return distributed.retire_finished_tiles(connection, self.now, force=True, **options)

    def holders_of(self, name: str, kind: str = "p") -> set[str]:
        with self.db() as connection:
            return self.holders(connection, digest_of(name, kind))

    def test_a_finished_field_loses_its_packets_and_bands_everywhere(self) -> None:
        self.root("done", "complete", tiles=("t1", "t2"))
        self.assertEqual(self.retire(), 2)
        for name in ("t1", "t2"):
            self.assertEqual((self.holders_of(name), self.holders_of(name, "b")), (set(), set()))
        with self.db() as connection:
            queued = connection.execute("SELECT node_name,reason,COUNT(*) FROM artifact_trim GROUP BY node_name").fetchall()
            self.assertEqual({row[0] for row in queued}, {"c", "d", "e"})
            self.assertEqual({row[1] for row in queued}, {"finished field"})
            self.assertEqual(sum(row[2] for row in queued), 12)          # 2 packets + 2 bands on 3 nodes
            # The result itself is untouched.
            self.assertEqual(self.holders(connection, digest_of("done", "r")), {"a", "b"})
        self.assertEqual(sorted(self.plan("c")), sorted(digest_of(n, k) for n in ("t1", "t2") for k in "pb"))

    def test_nothing_goes_before_the_retention_period_ends(self) -> None:
        self.root("done", "complete", finished_ago=HOURS)
        self.assertEqual(self.retire(), 0)
        self.assertEqual(self.holders_of("t"), set("cde"))

    def test_nothing_goes_unless_the_result_is_held_on_two_live_machines(self) -> None:
        self.root("done", "complete", result_copies="a")
        self.assertEqual(self.retire(), 0)
        with self.db() as connection:
            connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                               (digest_of("done", "r"), "b", "x", self.now))
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='b'", (self.now - 600,))
        self.assertEqual(self.retire(), 0)                             # the second holder is not live
        with self.db() as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=? WHERE node_name='b'", (self.now,))
        self.assertEqual(self.retire(), 1)

    def test_failed_attempts_of_a_finished_field_go_too_but_not_before(self) -> None:
        self.root("old", "failed", tiles=("t1", "t2"))
        self.assertEqual(self.retire(), 0)                             # nothing has finished: reuse_tiles may want them
        self.root("done", "complete", tiles=("t2", "t3"))               # t2 is shared by both attempts
        self.assertEqual(self.retire(), 3)
        self.assertEqual([self.holders_of(n) for n in ("t1", "t2", "t3")], [set()] * 3)

    def test_a_live_root_keeps_every_packet_it_references_including_shared_ones(self) -> None:
        self.root("done", "complete", tiles=("t1", "t2"))
        for state in ("waiting", "queued", "running", "paused"):
            with self.subTest(state=state):
                self.setUp()
                self.root("done", "complete", tiles=("t1", "t2"))
                self.root("again", state, finished_ago=None, tiles=("t2", "t9"))   # a rerun of the same field
                self.retire()
                self.assertEqual(self.holders_of("t1"), set())                       # only the finished root used it
                self.assertEqual(self.holders_of("t2"), set("cde"))                  # shared with the live root
                self.assertEqual(self.holders_of("t9"), set("cde"))

    def test_other_fields_are_untouched(self) -> None:
        self.root("done", "complete", tiles=("t1",))
        self.root("big", "paused", field=(13, 9), finished_ago=None, tiles=("t2",), side=4096)
        self.root("fail", "failed", field=(7, 5), tiles=("t3",))
        self.assertEqual(self.retire(), 1)
        self.assertEqual([self.holders_of(n) for n in ("t2", "t3")], [set("cde")] * 2)

    def test_retention_can_be_lengthened_or_switched_off(self) -> None:
        self.root("done", "complete", finished_ago=24 * HOURS)
        with self.db() as connection:
            connection.execute("UPDATE settings SET value=? WHERE key='tile_retention_seconds'", (str(48 * HOURS),))
        self.assertEqual(self.retire(), 0)
        with self.db() as connection:
            connection.execute("UPDATE settings SET value='-1' WHERE key='tile_retention_seconds'")
            connection.execute("UPDATE runs SET finished=0 WHERE run_id='root-done'")
        self.assertEqual(self.retire(), 0)
        self.assertEqual(self.holders_of("t"), set("cde"))

    def test_scans_are_spaced_out(self) -> None:
        self.root("first", "complete", tiles=("t1",))
        with self.db() as connection:
            self.assertEqual(distributed.retire_finished_tiles(connection, self.now), 1)
        self.root("second", "complete", tiles=("t2",))
        with self.db() as connection:
            self.assertEqual(distributed.retire_finished_tiles(connection, self.now + 10), 0)      # too soon
            later = self.now + distributed.RETIRE_SCAN_SECONDS + 1
            connection.execute("UPDATE nodes SET last_heartbeat=?", (later,))                    # the cluster is still up
            self.assertEqual(distributed.retire_finished_tiles(connection, later), 1)

    def test_a_full_batch_is_followed_by_another_scan_at_once(self) -> None:
        self.root("done", "complete", tiles=tuple(f"t{i}" for i in range(5)))
        with self.db() as connection, mock.patch.object(distributed, "RETIRE_BATCH", 2):
            counts = [distributed.retire_finished_tiles(connection, self.now + step) for step in range(4)]
        self.assertEqual(counts, [2, 2, 1, 0])

    def test_it_runs_from_the_scheduler_even_while_dispatch_is_stopped(self) -> None:
        self.root("done", "complete")
        with self.db() as connection:
            connection.execute("UPDATE settings SET value='stopped' WHERE key='campaign_state'")
            connection.execute("UPDATE settings SET value='0' WHERE key='tile_retire_scanned'")
            distributed.advance(connection, self.now)
        self.assertEqual(self.holders_of("t"), set())


def digest_of(name: str, kind: str) -> str:
    """A stable 64-hex name for an artifact: kind letter and name, padded."""

    import hashlib
    return hashlib.sha256(f"{kind}:{name}".encode()).hexdigest()


class DirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def make(self, run: str, token: str = "t", age: float = 0.0) -> Path:
        directory = self.work / run / token
        directory.mkdir(parents=True)
        (directory / "result.bin").write_bytes(b"x" * 100)
        stamp = time.time() - age
        for path in (directory / "result.bin", directory, directory.parent):
            os.utime(path, (stamp, stamp))
        return directory

    def test_a_finished_lease_takes_its_run_directory_with_it_unless_another_lease_remains(self) -> None:
        first, second = self.make("r", "one"), self.make("r", "two")
        agent.discard_run_directory(first)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        agent.discard_run_directory(second)
        self.assertFalse((self.work / "r").exists())
        agent.discard_run_directory(second)                                  # already gone: no error

    def sweep(self, states: dict, **options) -> tuple[int, list]:
        asked = []

        def fake(leader_url, route, request, *rest, **more):
            asked.append(request["run_ids"])
            return {"states": {run: states.get(run) for run in request["run_ids"]}}

        with mock.patch.object(agent, "request_json", fake):
            removed = agent.sweep_work("unused", {"node_name": "a", "session_id": ""}, self.work, **options)
        return removed, asked

    def test_sweep_removes_only_idle_directories_of_finished_or_unknown_runs(self) -> None:
        import uuid
        names = {key: str(uuid.uuid4()) for key in ("complete", "failed", "cancelled", "running", "queued", "paused",
                                                     "fresh", "unknown_old", "unknown_new")}
        old = agent.SWEEP_MIN_AGE + 60
        for key in ("complete", "failed", "cancelled", "running", "queued", "paused"):
            self.make(names[key], age=old)
        self.make(names["fresh"], age=60)                                     # touched a minute ago
        self.make(names["unknown_old"], age=agent.SWEEP_UNKNOWN_AGE + 60)
        self.make(names["unknown_new"], age=old)                              # unknown, but not yet a day old
        (self.work / ".dependency-cache").mkdir()
        (self.work / "notes").mkdir()                                         # not a run directory
        states = {names["complete"]: "complete", names["failed"]: "failed", names["cancelled"]: "cancelled",
                  names["running"]: "running", names["queued"]: "queued", names["paused"]: "paused",
                  names["fresh"]: "complete"}
        removed, asked = self.sweep(states)
        self.assertEqual(removed, 4)
        self.assertEqual({key for key in names if not (self.work / names[key]).exists()},
                         {"complete", "failed", "cancelled", "unknown_old"})
        self.assertTrue((self.work / ".dependency-cache").exists() and (self.work / "notes").exists())
        self.assertNotIn(names["fresh"], sum(asked, []))                      # recently touched ones are not even asked about

    def test_sweep_stops_at_its_limit_and_asks_in_bounded_batches(self) -> None:
        import uuid
        runs = [str(uuid.uuid4()) for _ in range(300)]
        for run in runs:
            self.make(run, age=agent.SWEEP_MIN_AGE + 60)
        removed, asked = self.sweep({run: "complete" for run in runs}, limit=250)
        self.assertEqual(removed, 250)
        self.assertEqual([len(batch) for batch in asked], [256])
        removed, asked = self.sweep({run: "complete" for run in runs})
        self.assertEqual(removed, 50)


class LeaderSweepRouteTests(LeaderCase):
    def test_states_of_known_and_unknown_runs(self) -> None:
        with self.db() as connection:
            for run, state in (("a" * 36, "complete"), ("b" * 36, "running")):
                connection.execute(
                    "INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created) VALUES(?,?,?,?,0,0,0)",
                    (run, run, "{}", state))
        answer = self.handler.dispatch_post("/v1/work-sweep", {"node_name": "a", "session_id": "",
                                                              "run_ids": ["a" * 36, "b" * 36, "c" * 36]})
        self.assertEqual(answer, {"states": {"a" * 36: "complete", "b" * 36: "running", "c" * 36: None}})

    def test_bad_requests_and_stale_sessions_are_refused(self) -> None:
        for ids in ("nope", ["short"], [1], ["x" * 36] * 257):
            with self.assertRaises(ValueError, msg=str(ids)[:30]):
                self.handler.dispatch_post("/v1/work-sweep", {"node_name": "a", "session_id": "", "run_ids": ids})
        with self.assertRaises(PermissionError):
            self.handler.dispatch_post("/v1/work-sweep", {"node_name": "a", "session_id": "old", "run_ids": []})


class ScratchClusterTests(unittest.TestCase):
    """Real leader and agents: finished runs leave no scratch directory behind."""

    def test_a_distributed_field_leaves_only_the_shared_cache_in_the_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cluster = Cluster(root)
            try:
                for name in ("a", "b"):
                    cluster.worker(name, 1)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 2, "two workers")
                queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": SPECIFICATION})

                def complete():
                    run = find_run(cluster.url, queued["run_id"])
                    if run["state"] == "failed":
                        raise AssertionError(run["error"])
                    return run if run["state"] == "complete" else None

                wait_until(complete, "distributed field", timeout=90)

                def clean():
                    left = [path for name in ("a", "b") for path in (root / name / "work").iterdir()
                            if not path.name.startswith(".")]
                    return True if not left else None

                wait_until(clean, "scratch removal", timeout=20)
                for name in ("a", "b"):
                    self.assertTrue((root / name / "blobs").exists())
            finally:
                cluster.close()


if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test disk reclamation.\nExample: python3 tests/test_reclaim.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
