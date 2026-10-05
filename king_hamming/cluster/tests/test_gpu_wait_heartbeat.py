#!/usr/bin/env python3
"""A tile queued for its host's GPU keeps reporting that it is alive."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver import distributed_solver


class GPUWaitHeartbeatTests(unittest.TestCase):
    def test_reports_waiting_every_few_seconds_and_passes_through_stop(self) -> None:
        moments = iter([0.0, 1.0, 4.9, 5.0, 7.0, 10.5])
        check = distributed_solver.gpu_wait_heartbeat(4096 * 4096, clock=lambda: next(moments))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            results = [check() for _ in range(6)]
        self.assertEqual(results, [False] * 6)
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(records), 3, "at 0, 5.0 and 10.5 seconds")
        self.assertEqual({(record["phase"], record["heartbeat"], record["done"], record["total"]) for record in records},
                         {("waiting for GPU", True, 0, 4096 * 4096)})
        distributed_solver.STOP = True
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(distributed_solver.gpu_wait_heartbeat(1, clock=lambda: 0.0)())
        finally:
            distributed_solver.STOP = False


class CPUFallbackPolicyTests(unittest.TestCase):
    def test_off_means_wait_for_the_gpu_however_long_it_takes(self) -> None:
        from dp_solver.tiles import tile
        rectangle = tile(29, 7, 4096, 50, 50)
        self.assertEqual(distributed_solver.gpu_wait_seconds(29, rectangle, 2, cpu_fallback=False), float("inf"))
        bounded = distributed_solver.gpu_wait_seconds(29, rectangle, 2, cpu_fallback=True)
        self.assertTrue(0 < bounded <= distributed_solver.MAX_GPU_WAIT_SECONDS)

    def test_the_leader_sends_the_setting_with_the_inputs_and_the_tile_keeps_it(self) -> None:
        import tempfile
        import leader
        from dp_solver import distributed
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            with leader.connect(database) as connection:
                self.assertFalse(distributed.cpu_fallback_allowed(connection), "off unless turned on")
                connection.execute("INSERT INTO settings(key,value) VALUES('dp_cpu_fallback','1')")
                self.assertTrue(distributed.cpu_fallback_allowed(connection))
        responses = iter([{"records": [1], "next": 1, "cpu_fallback": False}, {"records": [2], "next": None}])
        original_request, original_policy = distributed_solver.leader_request, dict(distributed_solver.POLICY)
        distributed_solver.leader_request = lambda arguments, route, body: next(responses)
        try:
            arguments = type("A", (), {"run_id": "r", "lease_token": "t"})()
            self.assertEqual(distributed_solver.descriptions(arguments), [1, 2])
            self.assertFalse(distributed_solver.POLICY["cpu_fallback"])
        finally:
            distributed_solver.leader_request = original_request
            distributed_solver.POLICY.update(original_policy)


if __name__ == "__main__":
    unittest.main()
