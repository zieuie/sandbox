#!/usr/bin/env python3
"""Check resource model agreement, runtime ordering, priorities and retained campaigns."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import leader
import adapters
from dp_solver import scheduling


# Compare Python scheduling counts with the independently implemented C estimator.
class EstimateTests(unittest.TestCase):
    """Test campaign coverage, deterministic ordering and practical admission bounds."""

    def test_matches_c_estimator(self) -> None:
        """Small and large supported fields use identical state and visit estimates."""

        for p, r in ((2, 3), (3, 5), (5, 3), (13, 5), (2, 31), (1621, 3)):
            with self.subTest(p=p, r=r):
                expected = json.loads(subprocess.check_output([str(ROOT.parent / "dp_solver" / "kh_estimate"), str(p), str(r), "--json"]))
                actual = scheduling.dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})
                self.assertEqual(actual["state_bytes"], expected["state_bytes"])
                self.assertEqual(actual["transition_bytes"], expected["transition_bytes"])
                if not expected["estimated_visits_overflow"]:
                    self.assertEqual(actual["raw_visits"], expected["estimated_visits"])

    def test_campaign_order_admission_and_unique_fields(self) -> None:
        """Generate every field fitting a small frontier exactly once in ascending cost."""

        entries = list(scheduling.campaign(max_state_bytes=100_000, max_visits=100_000))
        costs = [scheduling.dp_estimate(specification)["raw_visits"] for specification in entries]
        fields = [(item["arguments"]["p"], item["arguments"]["r"]) for item in entries]
        self.assertEqual(costs, sorted(costs))
        self.assertEqual(len(fields), len(set(fields)))
        self.assertIn((5, 3), fields)
        self.assertNotIn((7, 3), fields)
        self.assertTrue(all(scheduling.dp_estimate(item)["state_bytes"] <= 100_000 for item in entries))
        self.assertEqual(entries, list(scheduling.campaign(max_state_bytes=100_000, max_visits=100_000)))

    def test_invalid_inputs_and_extreme_limits(self) -> None:
        """Reject unsupported characteristics/degrees and unrepresentable command arguments."""

        for p, r in ((4, 3), (3, 4), (3, 31), (True, 3)):
            with self.assertRaises(ValueError):
                scheduling.dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})
        with self.assertRaises(ValueError):
            list(scheduling.campaign(max_visits=2**64))
        with self.assertRaises(ValueError):
            adapters.estimate_seconds({"program": "demo"}, float("nan"))
        self.assertEqual(list(scheduling.campaign(max_visits=1)), [])


# Exercise actual dispatch transactions rather than mirroring the SQL sort expression.
class QueueTests(unittest.TestCase):
    """Manual priorities override runtime order while duplicate and rerun history remain intact."""

    def test_runtime_order_priority_duplicates_and_upgrade(self) -> None:
        """Late cheap jobs run first; priority overrides and a retained rerun remain distinct."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            cheap = {"program": "dp", "arguments": {"p": 3, "r": 3}}
            slow = {"program": "dp", "arguments": {"p": 7, "r": 3}}
            middle = {"program": "dp", "arguments": {"p": 5, "r": 3}}
            jobs = [handler.dispatch_post("/v1/enqueue", {"specification": spec}) for spec in (slow, middle, cheap)]
            priority = handler.dispatch_post("/v1/enqueue", {"specification": slow, "rerun": True, "priority": 1})
            self.assertTrue(handler.dispatch_post("/v1/enqueue", {"specification": cheap})["reused"])
            expected = [priority["run_id"], jobs[2]["run_id"], jobs[1]["run_id"], jobs[0]["run_id"]]

            for index, run_id in enumerate(expected):
                name = f"worker-{index}"
                handler.dispatch_post("/v1/register", {"node_name": name})
                self.assertEqual(handler.dispatch_post("/v1/lease", {"node_name": name})["job"]["run_id"], run_id)

            # Startup recalibrates existing estimates without losing runs or active lease history.
            leader.initialize(database, 1800, visits_per_second=1_000_000)
            with leader.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 4)
                estimate = connection.execute("SELECT estimated_seconds FROM runs WHERE run_id=?", (jobs[2]["run_id"],)).fetchone()[0]
                self.assertAlmostEqual(estimate, 3**7 / 1_000_000)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM lease_history").fetchone()[0], 4)

    def test_multi_node_matching_reserves_and_numbers_whole_group(self) -> None:
        """Reserve four matching nodes and give every peer a stable shard identity."""

        from matching_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            sessions = {}
            for index in range(4):
                name = f"match-{index}"
                sessions[name] = f"session-{index}"
                handler.dispatch_post("/v1/register", {
                    "node_name": name, "session_id": sessions[name],
                    "address": f"http://127.0.0.1:{9000 + index}", "cpu_set": "0",
                })
            job_spec = specification(ROOT.parent / "examples/3_3.khdp",
                                     "1,2,0,1", 1, 2**31,
                                     distributed=True, workers=4)
            queued = handler.dispatch_post("/v1/enqueue", {"specification": job_spec})
            job = handler.dispatch_post("/v1/lease", {
                "node_name": "match-2", "session_id": sessions["match-2"],
            })["job"]
            self.assertEqual(job["run_id"], queued["run_id"])
            self.assertEqual([item["node_name"] for item in job["reserved_workers"]],
                             ["match-0", "match-1", "match-3"])
            for expected, name in enumerate(("match-0", "match-1", "match-3"), 1):
                authorized = handler.dispatch_post("/v1/peer-authorize", {
                    "node_name": name, "session_id": sessions[name],
                    "run_id": queued["run_id"], "lease_token": job["lease_token"],
                })
                self.assertEqual((authorized["worker_index"], authorized["worker_count"]),
                                 (expected, 4))
            identity = {"run_id": queued["run_id"], "lease_token": job["lease_token"]}
            handler.dispatch_post("/v1/resource-usage", {
                **identity, "component": "coordinator", "shard_index": -1,
                "cpu_microseconds": 100, "peak_rss_bytes": 200,
            })
            handler.dispatch_post("/v1/resource-usage", {
                **identity, "component": "shard", "shard_index": 2,
                "cpu_microseconds": 300, "peak_rss_bytes": 400,
            })
            # Replayed reports may only increase lifetime/peak counters.
            handler.dispatch_post("/v1/resource-usage", {
                **identity, "component": "shard", "shard_index": 2,
                "cpu_microseconds": 250, "peak_rss_bytes": 350,
            })
            with leader.connect(database) as connection:
                usage = list(connection.execute(
                    "SELECT node_name,component,shard_index,cpu_microseconds,peak_rss_bytes "
                    "FROM resource_usage ORDER BY shard_index"
                ))
            self.assertEqual(tuple(usage[0]), ("match-2", "coordinator", -1, 100, 200))
            self.assertEqual(tuple(usage[1]), ("match-1", "shard", 2, 300, 400))
            with self.assertRaises(ValueError):
                handler.dispatch_post("/v1/resource-usage", {
                    **identity, "component": "shard", "shard_index": 4,
                    "cpu_microseconds": 1, "peak_rss_bytes": 1,
                })
            with self.assertRaises(PermissionError):
                handler.dispatch_post("/v1/peer-authorize", {
                    "node_name": "match-0", "session_id": sessions["match-0"],
                    "run_id": queued["run_id"], "lease_token": job["lease_token"],
                    "worker_index": 3, "worker_count": 4,
                })


# Help-only invocation performs no test workloads or network requests.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test runtime-ordered campaigns.\nExample: python3 tests/test_scheduling.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
