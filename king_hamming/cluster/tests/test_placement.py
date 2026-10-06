#!/usr/bin/env python3
"""Earliest-completion placement of DP tiles (cluster/placement.py), on and off."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import leader  # noqa: E402
import placement  # noqa: E402
from dp_solver import distributed  # noqa: E402

SPECIFICATION = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7, "threads": 1}}
RTX = {"index": 0, "name": "RTX 3060 Laptop", "arch": 86, "total_bytes": 6 * 1024**3}
P600 = {"index": 0, "name": "Quadro P600", "arch": 61, "total_bytes": 2 * 1024**3}


def candidate(row: int, column: int, root: str = "root", priority: int = 0, phase: str = "queued",
              peers: int = 0, created: float = 0.0, estimate: float | None = 1.0) -> dict:
    return {"run_id": f"{root}-{row}-{column}", "priority": priority, "progress_phase": phase, "peers": peers,
            "created": created, "estimated_seconds": estimate,
            "specification": json.dumps({"program": "dp_tile", "arguments": {
                "parent_run_id": root, "row": row, "column": column}})}


class OrderTests(unittest.TestCase):
    def test_lowest_diagonal_first_within_the_leaders_own_ties(self) -> None:
        tiles = [candidate(3, 3, created=1, estimate=0.5), candidate(0, 5, created=2), candidate(2, 1, created=3),
                 candidate(9, 9, priority=5, created=9)]
        ordered = [item["run_id"] for item in placement.order(tiles)]
        # Priority still wins; then diagonal 3, 5, 6 regardless of the estimate.
        self.assertEqual(ordered, ["root-9-9", "root-2-1", "root-0-5", "root-3-3"])
        reconstruct = {**candidate(9, 9), "run_id": "rebuild", "progress_phase": "reconstructing",
                       "specification": json.dumps({"program": "dp_distributed", "arguments": {}})}
        self.assertEqual(placement.order(tiles + [reconstruct])[0]["run_id"], "rebuild")


class LeaseTests(unittest.TestCase):
    """A small field on a fast and a slow GPU machine, through the real lease path."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name, device in (("fast", RTX), ("slow", P600)):
            self.handler.dispatch_post("/v1/register", {
                "node_name": name, "address": f"http://{name}:8042", "cpu_set": "0,1,2,3",
                "memory_bytes": 16 * 1024**3, "gpus": [device],
                "slots": [{"slot_id": index, "cpu_set": str(index)} for index in range(4)]})
        self.root = self.handler.dispatch_post("/v1/enqueue", {"specification": SPECIFICATION})["run_id"]
        placement.MODEL.__init__()
        now = time.time()
        with leader.connect(self.database) as connection:
            distributed.advance(connection, now)
            # Recorded history on this field: the fast GPU takes 5 s a tile, the slow one 57 s.
            for index in range(12):
                for node, kernel in (("fast", 5.0), ("slow", 57.0)):
                    self.fake_run(connection, f"history-{node}-{index}", parent=self.root, state="complete",
                                  started=now - 600, finished=now - 600 + kernel + 3, node_name=node,
                                  progress_details=json.dumps({"engine": "gpu", "kernel_seconds": kernel,
                                                               "fetch_seconds": 1.0}))

    def fake_run(self, connection, run_id: str, parent: str, **values) -> None:
        """A run row cloned from a real tile run, so every required column is filled."""
        template = dict(connection.execute(
            "SELECT * FROM runs WHERE parent_run_id=? AND json_extract(specification,'$.program')='dp_tile' LIMIT 1",
            (self.root,)).fetchone())
        template.update(run_id=run_id, parent_run_id=parent, lease_token=None, **values)
        template["specification"] = json.dumps({"program": "dp_tile", "arguments": {"tag": run_id}})
        columns = ",".join(template)
        connection.execute(f"INSERT INTO runs({columns}) VALUES({','.join('?' for _ in template)})",
                           tuple(template.values()))

    def lease(self, node: str):
        return self.handler.dispatch_post("/v1/lease", {"node_name": node, "slot_id": 0}).get("job")

    def set_placement(self, value: str) -> None:
        with leader.connect(self.database) as connection:
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (placement.SETTING, value))
        placement.MODEL.__init__()

    def test_pull_gives_the_critical_tile_to_whoever_asks(self) -> None:
        job = self.lease("slow")
        self.assertIsNotNone(job)
        self.assertEqual(job["specification"]["arguments"]["row"] + job["specification"]["arguments"]["column"], 0)

    def test_ect_leaves_a_critical_tile_for_the_faster_gpu(self) -> None:
        self.set_placement("ect")
        self.assertIsNone(self.lease("slow"), "the slow GPU must not take the only (critical) tile")
        job = self.lease("fast")
        self.assertIsNotNone(job)
        self.assertEqual(job["specification"]["program"], "dp_tile")

    def test_ect_lets_the_slow_gpu_take_it_when_the_fast_one_is_backed_up(self) -> None:
        self.set_placement("ect")
        with leader.connect(self.database) as connection:
            for index in range(4):   # four queued tiles: fast still wins (7 + 20 + 7 = 34 s against 60 s)
                self.fake_run(connection, f"busy-{index}", parent=self.root, state="running", started=time.time(),
                              node_name="fast", progress_phase="waiting for GPU")
        self.assertIsNone(self.lease("slow"))
        placement.MODEL.__init__()
        with leader.connect(self.database) as connection:
            for index in range(4, 10):   # ten: 7 + 50 + 7 = 64 s, so the slow GPU's 60 s is sooner
                self.fake_run(connection, f"busy-{index}", parent=self.root, state="running", started=time.time(),
                              node_name="fast", progress_phase="waiting for GPU")
        self.assertIsNotNone(self.lease("slow"))

    def test_no_history_holds_nothing_back(self) -> None:
        self.set_placement("ect")
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM runs WHERE run_id LIKE 'history-slow-%'")
        self.assertIsNotNone(self.lease("slow"))

    def test_completion_estimate(self) -> None:
        model = placement.Model()
        model.kernels[("n", "r")] = 10.0
        model.overheads["n"] = (2.0, 3.0)
        node = {"node_name": "n", "slots_json": json.dumps([{}, {}])}
        self.assertEqual(placement.completion(node, "r", {}, model), 2.0 + 10.0 + 3.0)
        busy = {"n": {"before": 1, "computing": 1, "total": 2}}
        # Full: wait for a slot (10 + 3), then 1.5 kernels of backlog before ours.
        self.assertEqual(placement.completion(node, "r", busy, model), 13.0 + 15.0 + 10.0 + 3.0)
        self.assertIsNone(placement.completion({"node_name": "other"}, "r", {}, model))


if __name__ == "__main__":
    unittest.main()


class SimulatorTests(unittest.TestCase):
    """dp_solver/placement_sim.py on a synthetic field: one fast GPU and several slow ones."""

    def test_ect_beats_pull_and_tightens_the_tail(self) -> None:
        from dp_solver import placement_sim as sim
        size = 30
        keys = [(row, column) for row in range(size) for column in range(size)]
        predecessors = {(r, c): [k for k in ((r - 1, c), (r, c - 1), (r - 1, c - 1)) if min(k) >= 0]
                        for r, c in keys}
        successors = {key: [] for key in keys}
        for key, items in predecessors.items():
            for item in items:
                successors[item].append(key)
        field_ = sim.Field(size, size, {key: 5.0 for key in keys}, predecessors, successors, set())

        def fleet():
            return [sim.Machine("fast", "3060", 7, 0.3, 1.7, 1.0)] + \
                [sim.Machine(f"slow{i}", "P600", 4, 2.5, 4.5, 12.0) for i in range(8)]
        today = sim.simulate(field_, fleet(), "today", cap=8)
        ect = sim.simulate(field_, fleet(), "ect", cap=8)
        self.assertEqual(today["tiles"], size * size)
        self.assertLess(ect["hours"], today["hours"])
        self.assertLess(ect["tail_minutes"], today["tail_minutes"])
