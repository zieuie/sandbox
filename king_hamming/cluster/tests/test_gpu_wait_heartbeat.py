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


if __name__ == "__main__":
    unittest.main()
