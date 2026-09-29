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

    def test_simultaneous_restart_preserves_tile_frontier(self) -> None:
        """Restart every agent after partial replication and recover its retained artifact inventory."""

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

                wait_until(partial,"partial replicated tile frontier",timeout=30)
                request_json(cluster.url,"POST","/v1/control",{"state":"stopped"})
                for worker in workers:
                    worker.kill()
                    worker.wait(timeout=5)
                time.sleep(1.3)
                for name in ("a","b","c"):
                    cluster.worker(name,1)
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
