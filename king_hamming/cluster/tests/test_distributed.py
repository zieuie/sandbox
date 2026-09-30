#!/usr/bin/env python3
"""Test exact immutable DP tiles through the ordinary replicated queue and agent leases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from test_recovery import Cluster
from test_integration import find_run, request_json, wait_until


# Exercise the real HTTP leader, ordinary agents, C tile kernel and peer artifact storage.
class DistributedTests(unittest.TestCase):
    """A parent split becomes ready only after its predecessor artifacts are replicated."""

    def test_one_agent_supervises_two_disjoint_tile_slots(self) -> None:
        """A real agent runs separate pinned tile processes without sharing CPUs."""

        if len(os.sched_getaffinity(0)) < 2:
            self.skipTest("two allowed CPUs required")
        with tempfile.TemporaryDirectory() as temporary:
            cluster = Cluster(Path(temporary))
            try:
                cluster.worker("slotted", 2, slots=2)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 1,
                           "slotted worker")
                for p in (13, 17):
                    request_json(cluster.url, "POST", "/v1/enqueue", {"specification": {
                        "program": "dp_distributed", "arguments": {
                            "p": p, "r": 3, "tile_side": 256, "threads": 2, "max_cpus": 1,
                            "max_visits": 9_000_000_000_000_000_000,
                            "max_tile_bytes": 2 * 1024**3,
                        },
                    }})

                def concurrent():
                    with sqlite3.connect(cluster.database) as connection:
                        rows = connection.execute(
                            "SELECT slot_id,assigned_cpu_set FROM runs "
                            "WHERE node_name='slotted' AND state='running'"
                        ).fetchall()
                    return rows if len(rows) >= 2 else None

                rows = wait_until(concurrent, "two concurrent tile slots", timeout=30)
                self.assertEqual(len({row[0] for row in rows}), len(rows))
                self.assertEqual(len({row[1] for row in rows}), len(rows))
            finally:
                cluster.close()

    def test_uncapped_tile_uses_every_registered_cpu(self) -> None:
        """The agent passes an expanded allocation through Python to the C pool."""
        count = min(4, len(os.sched_getaffinity(0)))
        if count < 2:
            self.skipTest("two allowed CPUs required")
        with tempfile.TemporaryDirectory() as temporary:
            cluster = Cluster(Path(temporary))
            try:
                cluster.worker("team", count, slots=count)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 1,
                           "team worker")
                request_json(cluster.url, "POST", "/v1/enqueue", {"specification": {
                    "program": "dp_distributed", "arguments": {
                        "p": 31, "r": 3, "tile_side": 512, "threads": 1,
                        "max_visits": 9_000_000_000_000_000_000,
                    }}})

                def native_team():
                    with sqlite3.connect(cluster.database) as connection:
                        rows = connection.execute(
                            "SELECT assigned_cpu_set,progress_details,error,state "
                            "FROM runs WHERE parent_run_id IS NOT NULL").fetchall()
                    for cpus, details, error, state in rows:
                        self.assertNotEqual(state, "failed", error)
                        if details and json.loads(details).get("threads") == count:
                            return cpus
                    return None

                cpus = wait_until(native_team, "native multicore team", timeout=30)
                self.assertEqual(len(cpus.split(",")), count)
            finally:
                cluster.close()

    def test_stop_replace_revalidate_resume_preserves_tile_frontier(self) -> None:
        """Replace leader and every agent while stopped, then resume the exact durable frontier."""

        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            cluster=Cluster(root)
            try:
                workers=[cluster.worker(name,1) for name in ("a","b","c")]
                wait_until(lambda:len(request_json(cluster.url,"GET","/v1/status")["nodes"])==3,"three restart workers")
                specification={"program":"dp_distributed","arguments":{"p":5,"r":5,"tile_side":32,"threads":1}}
                queued=request_json(cluster.url,"POST","/v1/enqueue",{"specification":specification})

                def partial():
                    """Observe a retained replicated frontier before stopping the campaign."""

                    row=find_run(cluster.url,queued["run_id"])
                    if row["state"]=="failed":
                        raise AssertionError(row["error"])
                    return row if 0<row["progress_done"]<row["progress_total"] else None

                before=wait_until(partial,"partial replicated tile frontier",timeout=30)
                request_json(cluster.url,"POST","/v1/control",{"state":"stopped"})
                wait_until(lambda:not any(run["state"]=="running" for run in
                                          request_json(cluster.url,"GET","/v1/status")["runs"]),
                           "all leases quiescent",timeout=30)
                with sqlite3.connect(cluster.database) as connection:
                    durable_before={row[0] for row in connection.execute(
                        "SELECT child.artifact_hash FROM distributed_tiles tile "
                        "JOIN runs child ON child.run_id=tile.child_run_id "
                        "WHERE tile.parent_run_id=? AND child.state='complete' "
                        "AND (SELECT COUNT(*) FROM replicas replica "
                        "WHERE replica.artifact_hash=child.artifact_hash)>=2",
                        (queued["run_id"],))}
                self.assertTrue(durable_before)
                for worker in workers:
                    worker.kill()
                    worker.wait(timeout=5)
                original_leader=cluster.processes[0]
                original_leader.terminate()
                original_leader.wait(timeout=5)
                cluster.start("leader-replacement", [str(ROOT/"leader.py"),"serve",
                              "--database",str(cluster.database),"--listen",
                              cluster.url.removeprefix("http://"),"--lease-seconds","1.2",
                              "--checkpoint-seconds","0"])

                def replacement_ready():
                    """Allow the replacement process its ordinary socket-bind startup window."""

                    try:
                        return request_json(cluster.url,"GET","/v1/health")["ok"]
                    except OSError:
                        return False

                wait_until(replacement_ready,"replacement leader")
                for name in ("a","b","c"):
                    cluster.worker(name,1)

                def inventory_complete():
                    """Return durable hashes after every new storage session has proved ownership."""
                    with sqlite3.connect(cluster.database,timeout=5) as connection:
                        pending=connection.execute(
                            "SELECT COUNT(*) FROM node_revalidation").fetchone()[0]
                        sessions=connection.execute(
                            "SELECT COUNT(DISTINCT session_id) FROM nodes "
                            "WHERE storage_validation_mode='verified'").fetchone()[0]
                        durable={row[0] for row in connection.execute(
                            "SELECT child.artifact_hash FROM distributed_tiles tile "
                            "JOIN runs child ON child.run_id=tile.child_run_id "
                            "WHERE tile.parent_run_id=? AND child.state='complete' "
                            "AND (SELECT COUNT(*) FROM replicas replica "
                            "WHERE replica.artifact_hash=child.artifact_hash)>=2",
                            (queued["run_id"],))}
                    return durable if pending==0 and sessions==3 and durable_before<=durable else None

                durable_after=wait_until(inventory_complete,"batched retained inventory",timeout=30)
                self.assertEqual(durable_before,durable_after)
                request_json(cluster.url,"POST","/v1/control",{"state":"running"})

                def complete():
                    """Return only a completed recovered parent, surfacing any bounded engine failure."""

                    row=find_run(cluster.url,queued["run_id"])
                    if row["state"]=="failed":
                        raise AssertionError(row["error"])
                    return row if row["state"]=="complete" else None

                finished=wait_until(complete,"distributed DP after all-agent restart",timeout=90)
                with urlopen(finished["artifact_location"]) as response:
                    document=json.loads(response.read())
                reference=root/"reference.json"
                subprocess.run([str(ROOT.parent/"dp_solver"/"kh_dp_local"),"5","5","--raw-transitions","--work-dir",str(root/"raw"),"-o",str(reference)],check=True,capture_output=True)
                self.assertEqual(document,json.loads(reference.read_text()))
                with sqlite3.connect(cluster.database) as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM distributed_tiles WHERE parent_run_id=?",(queued["run_id"],)).fetchone()[0],16)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(DISTINCT row||','||column) FROM distributed_tiles "
                        "WHERE parent_run_id=?",(queued["run_id"],)).fetchone()[0],16)
                    self.assertGreaterEqual(finished["progress_done"],before["progress_done"])
            finally:
                cluster.close()

    def test_real_queue_tiles_and_reconstruction_match_reference(self) -> None:
        """Three normal workers complete a clipped multi-wave split with exact ordered choices."""

        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            cluster=Cluster(root)
            try:
                for name in ("a","b","c"):
                    cluster.worker(name,1)
                wait_until(lambda:len(request_json(cluster.url,"GET","/v1/status")["nodes"])==3,"three tile workers")
                specification={"program":"dp_distributed","arguments":{"p":5,"r":3,"tile_side":7,"threads":1,"artifact_format":"KHD1"}}
                queued=request_json(cluster.url,"POST","/v1/enqueue",{"specification":specification})

                def complete():
                    """Surface parent failure immediately rather than hiding it as a timeout."""

                    run=find_run(cluster.url,queued["run_id"])
                    if run["state"]=="failed":
                        raise AssertionError(run["error"])
                    return run if run["state"]=="complete" else None

                finished=wait_until(complete,"ordinary queued distributed DP",timeout=90)
                with urlopen(finished["artifact_location"]) as response:
                    raw=response.read()
                    sys.path.insert(0,str(ROOT.parent/"dp_solver"))
                    from artifacts import decode_dp
                    artifact=decode_dp(raw)
                    self.assertEqual(len(raw),56)
                reference=root/"reference.json"
                subprocess.run([str(ROOT.parent/"dp_solver"/"kh_dp_local"),"5","3","--raw-transitions","--work-dir",str(root/"raw"),"-o",str(reference)],check=True,capture_output=True)
                self.assertEqual(artifact,json.loads(reference.read_text()))
                with sqlite3.connect(cluster.database) as connection:
                    children=list(connection.execute("SELECT r.state,r.node_name,r.artifact_hash FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id WHERE t.parent_run_id=?",(queued["run_id"],)))
                    self.assertEqual(len(children),16)
                    self.assertTrue(all(state=="complete" for state,_,_ in children))
                    self.assertGreaterEqual(len({node for _,node,_ in children}),2)
                    for _,_,digest in children:
                        self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM replicas WHERE artifact_hash=?",(digest,)).fetchone()[0],2)
            finally:
                cluster.close()


# Empty invocation is descriptive and never starts cluster processes.
if __name__=="__main__":
    if "--run" not in sys.argv:
        print("Test queued distributed DP tiles.\nExample: python3 tests/test_distributed.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
