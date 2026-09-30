#!/usr/bin/env python3
"""Check concise and verbose operator status rendering."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kh


def run(run_id: str, state: str, program: str, arguments: dict,
        parent: str | None = None) -> dict:
    """Return one complete synthetic status row."""
    return {
        "run_id": run_id, "parent_run_id": parent, "state": state,
        "specification": json.dumps({"program": program, "arguments": arguments}),
        "node_name": "node-1" if state == "running" else None,
        "progress_done": 49, "progress_total": 100, "progress_units": "cells",
        "progress_phase": "tiles", "progress_message": "3/4 replicated tiles",
        "progress_checkpoint_done": 49, "replicated_checkpoint_done": 49,
        "checkpoint_replicas": 2, "restored_done": 0, "lease_attempt": 1,
        "recovery_count": 0, "retained_checkpoints": 1, "retired_checkpoints": 0,
        "estimated_seconds": 2.0, "last_solver_heartbeat": None,
        "last_checkpoint_at": None, "solver_health": state,
        "resource_usage": [], "artifact_location": None,
        "progress_details": '{"depth":3,"edges":4096}',
    }


class StatusTests(unittest.TestCase):
    """Default output is useful and bounded; verbose retains diagnostics."""

    def test_concise_status_has_array_and_tile_progress_without_history(self) -> None:
        root = run("dp-root", "waiting", "dp_distributed",
                   {"p": 3, "r": 3, "tile_side": 4})
        child = run("tile-child", "running", "dp_tile",
                    {"p": 3, "r": 3, "row": 0, "column": 0}, "dp-root")
        complete = run("old", "complete", "dp", {"p": 2, "r": 3})
        status = {
            "campaign_state": "running", "checkpoint_seconds": 1800,
            "lease_seconds": 60, "checkpoint_keep": 3,
            "scheduler": {"status": "healthy"},
            "nodes": [{"node_name": "node-1", "state": "healthy", "cpu_set": "0",
                       "compute_enabled": True, "reserved_for": None},
                      {"node_name": "node-2", "state": "healthy", "cpu_set": "0",
                       "compute_enabled": True, "reserved_for": None,
                       "idle_reason": "no dependency-ready tile"}],
            "artifacts": [{"artifact_hash": "secret-history", "replicas": 2,
                           "target_replicas": 2}],
            "runs": [root, child, complete],
        }
        output = StringIO()
        with redirect_stdout(output):
            kh.print_status(status)
        text = output.getvalue()
        self.assertIn("permutation=rows-pending-DP x 28", text)
        self.assertIn("tiles=3 complete, 1 pending, 1 active", text)
        self.assertIn("idle=no dependency-ready tile", text)
        self.assertNotIn("secret-history", text)
        self.assertNotIn("calculation=2^3 dp", text)
        self.assertNotIn("resources", text)
        self.assertNotIn("activity depth=3", text)

        verbose = StringIO()
        with redirect_stdout(verbose):
            kh.print_status(status, verbose=True)
        verbose_text = verbose.getvalue()
        self.assertIn("secret-history", verbose_text)
        self.assertIn("calculation=2^3 dp", verbose_text)
        self.assertIn("health=", verbose_text)
        self.assertIn("activity depth=3 edges=4096", verbose_text)


if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test status rendering. Example: python3 test_status.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
