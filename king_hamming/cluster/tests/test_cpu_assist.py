#!/usr/bin/env python3
"""A tile that finds its host's GPU busy may compute on the node's idle CPUs instead."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import gpus
from dp_solver import distributed_solver
from dp_solver.tiles import tile
from test_integration import find_run, request_json, wait_until
from test_recovery import Cluster


class AssistPlanTests(unittest.TestCase):
    def plan(self, p, r, side, row, column, lease, node, **environment):
        values = {"KH_NODE_CPUS": ",".join(map(str, node)), "KH_CPU_ASSIST": "1", **environment}
        with mock.patch.dict(os.environ, values):
            return distributed_solver.assist_plan(p, tile(p, r, side, row, column), lease)

    def test_light_tiles_are_left_to_the_ordinary_rule(self) -> None:
        self.assertIsNone(self.plan(7, 13, 4096, 30, 30, [2, 3], list(range(16))))   # about 16 s on two CPUs
        self.assertIsNone(self.plan(5, 13, 1024, 16, 16, [2, 3], list(range(16))))

    def test_borrows_the_nodes_idle_cpus_up_to_eight_threads(self) -> None:
        wide = self.plan(29, 7, 4096, 30, 30, [2, 3], list(range(16)))      # about 1,380 s on two CPUs
        self.assertEqual(wide[:2], [2, 3])
        self.assertEqual(len(wide), 8)
        self.assertEqual(len(set(wide)), 8)

    def test_no_assist_when_off_unknown_or_no_wider_than_the_lease(self) -> None:
        node = [0, 1, 2, 3]
        self.assertIsNone(self.plan(29, 7, 4096, 30, 30, [0, 1], node, KH_CPU_ASSIST="0"))
        with mock.patch.dict(os.environ, {"KH_NODE_CPUS": "0,1,2,3"}):
            os.environ.pop("KH_CPU_ASSIST", None)           # the default is off
            self.assertIsNone(distributed_solver.assist_plan(29, tile(29, 7, 4096, 30, 30), [0, 1]))
        self.assertIsNone(self.plan(29, 7, 4096, 30, 30, [0, 1], []))
        self.assertIsNone(self.plan(29, 7, 4096, 30, 30, [0, 1], [0, 1]))
        with mock.patch.dict(os.environ, {"KH_NODE_CPUS": "x,y"}):
            self.assertIsNone(distributed_solver.assist_plan(29, tile(29, 7, 4096, 30, 30), [0, 1]))

    def test_a_tile_too_slow_even_with_all_cpus_is_left_to_the_gpu(self) -> None:
        # 3^29 style tiles would take hours on eight CPUs.
        self.assertIsNone(self.plan(199, 3, 512, 70, 70, [0, 1], list(range(16))))


class AssistSlotTests(unittest.TestCase):
    def test_slots_are_limited_and_released(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(gpus, "LOCK_DIRECTORY", Path(directory)):
            first, second, third = (gpus.AssistSlot(2) for _ in range(3))
            self.assertTrue(first.acquire())
            self.assertTrue(second.acquire())
            self.assertFalse(third.acquire())
            first.release()
            self.assertTrue(third.acquire())
            self.assertFalse(gpus.AssistSlot(0).acquire())


class AssistClusterTests(unittest.TestCase):
    """Real leader, agent and CPU kernel: with the GPU held by someone else, tiles assist and stay exact."""

    def test_tiles_use_idle_cpus_while_the_gpu_is_busy_and_the_split_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            locks = root / "locks"
            locks.mkdir()
            environment = {"KH_DISABLE_GPU_DP": "0", "KH_GPU_LOCK_DIR": str(locks), "KH_CPU_ASSIST": "1", "KH_CPU_ASSIST_SLOTS": "2",
                           "KH_CPU_ASSIST_MIN_SECONDS": "0"}   # these tiles are tiny
            with mock.patch.dict(os.environ, environment), mock.patch.object(gpus, "LOCK_DIRECTORY", locks):
                holder = gpus.DeviceLock(0)
                self.assertTrue(holder.acquire(1.0))          # someone else is using the GPU
                cluster = Cluster(root)
                try:
                    # CPU assist is a use of the CPUs, which the leader forbids unless dp_cpu_fallback is on.
                    import sqlite3
                    with sqlite3.connect(cluster.database, timeout=30) as connection:
                        connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('dp_cpu_fallback','1')")
                    cluster.worker("a", 4, 2)
                    wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 1, "worker")
                    specification = {"program": "dp_distributed", "arguments": {
                        "p": 5, "r": 3, "tile_side": 7, "threads": 1, "artifact_format": "KHD1"}}
                    queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": specification})

                    def complete():
                        run = find_run(cluster.url, queued["run_id"])
                        if run["state"] == "failed":
                            raise AssertionError(run["error"])
                        return run if run["state"] == "complete" else None

                    finished = wait_until(complete, "distributed DP with CPU assist", timeout=150)
                    sys.path.insert(0, str(ROOT.parent / "dp_solver"))
                    from artifacts import decode_dp
                    from urllib.request import urlopen
                    with urlopen(finished["artifact_location"]) as response:
                        artifact = decode_dp(response.read())
                    reference = root / "reference.json"
                    subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), "5", "3", "--raw-transitions",
                                    "--work-dir", str(root / "raw"), "-o", str(reference)],
                                   check=True, capture_output=True)
                    self.assertEqual(artifact, json.loads(reference.read_text()))
                    with sqlite3.connect(cluster.database) as connection:
                        engines = [json.loads(text) for (text,) in connection.execute(
                            "SELECT r.progress_details FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                            "WHERE t.parent_run_id=?", (queued["run_id"],))]
                    assisted = [item for item in engines if item.get("engine") == "cpu-assist"]
                    self.assertTrue(assisted, [item.get("engine") for item in engines])
                    self.assertTrue(all(item["assist_threads"] > 2 for item in assisted))
                finally:
                    cluster.close()
                    holder.release()


if __name__ == "__main__":
    unittest.main()
