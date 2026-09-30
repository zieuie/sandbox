#!/usr/bin/env python3
"""Check the pure before/after canary report without waiting or contacting a cluster."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark_canary import report


class CanaryTests(unittest.TestCase):
    def test_progress_resources_and_idle_reasons(self):
        run = {"run_id": "r", "state": "running", "progress_done": 10,
               "progress_units": "cells", "resource_usage": []}
        before = {"runs": [run], "nodes": []}
        after_run = {**run, "progress_done": 30, "tile_counts": {"ready": 2},
                     "resource_usage": [{"node_name": "n1", "component": "solver",
                         "shard_index": 0, "assigned_cpu_utilization": .75,
                         "sample_seconds": 10, "peak_rss_bytes": 100}]}
        after = {"campaign_state": "running", "schema_version": 4,
                 "scheduler": {"status": "healthy"}, "runs": [after_run],
                 "nodes": [{"idle_reason": "running"},
                           {"idle_reason": "memory/CPU admission"}]}
        result = report(before, after, 10)
        self.assertEqual(result["progress"][0]["per_second"], 2)
        self.assertEqual(result["resources"][0]["assigned_cpu_utilization"], .75)
        self.assertEqual(result["idle_reasons"], {"memory/CPU admission": 1})


if __name__ == "__main__":
    unittest.main()
