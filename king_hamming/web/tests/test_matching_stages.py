#!/usr/bin/env python3
"""Block GPU matching runs: stages from the bridge's record, the agent's verification, and health."""

from __future__ import annotations

import json
import unittest

import fixture  # noqa: F401  (sets up the import path)
import matching  # noqa: E402

RECORD = {"stages": [
    {"key": "gpu_wait", "started": 100, "finished": 101, "done": 0, "total": None, "updated": 101},
    {"key": "field", "started": 101, "finished": 160, "done": 10, "total": 10, "updated": 160},
    {"key": "blocks", "started": 160, "finished": 300, "done": 4, "total": 4, "updated": 300},
    {"key": "write", "started": 300, "finished": 400, "done": 8, "total": 8, "updated": 400},
    {"key": "publish", "started": 400, "finished": 410, "done": 5, "total": 5, "updated": 410},
    {"key": "bogus", "started": 1}], "trace": [[0, 8], [1, 6], [2, 4], [3, 2], [4, 0]]}


def run(**changes) -> dict:
    return {"state": "running", "progress_phase": "verifying", "last_solver_heartbeat": 500,
            "finished": None, **changes}


class BlockStageTests(unittest.TestCase):
    def test_verification_follows_publication(self) -> None:
        stages = matching.block_stages(run(), RECORD)
        self.assertEqual([item["key"] for item in stages],
                         ["gpu_wait", "field", "blocks", "write", "publish", "verify"])
        self.assertEqual((stages[-1]["started"], stages[-1]["finished"]), (410, None))
        done = matching.block_stages(run(state="complete", progress_phase="complete", finished=900), RECORD)
        self.assertEqual((done[-1]["started"], done[-1]["finished"]), (410, 900))
        writing = matching.block_stages(run(progress_phase="writing the result"), RECORD)
        self.assertEqual(writing[-1]["key"], "publish")

    def test_a_quiet_stage_that_still_reports_is_not_stalled(self) -> None:
        row = {"run_id": "r", "state": "running", "progress_phase": "writing the result",
               "progress_message": json.dumps(RECORD), "progress_done": 8, "progress_total": 8,
               "last_solver_heartbeat": 500, "finished": None, "started": 100, "created": 90,
               "specification": json.dumps({"program": "match_gpu_blocks", "arguments": {}}),
               "node_name": None, "gpu_index": 0, "last_progress_at": 300, "error": None}

        class Connection:
            def execute(self, sql, parameters=()):
                return iter([row]) if sql.startswith("SELECT * FROM runs") else iter([])

        def describe(_run):
            return {"program": "match_gpu_blocks", "field": [13, 9]}

        built = matching.build_matching(Connection(), {}, describe, lambda _run, _now: "stalled", 600)
        self.assertEqual(built["runs"][0]["health"], "responding")      # write reported at 400
        built = matching.build_matching(Connection(), {}, describe, lambda _run, _now: "stalled", 2000)
        self.assertEqual(built["runs"][0]["health"], "stalled")
        self.assertEqual(built["runs"][0]["phases"][-1][:2], [4, 8])   # live burndown from the trace


if __name__ == "__main__":
    unittest.main()
