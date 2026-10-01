#!/usr/bin/env python3
"""Memory-first routing, safe continuous reconciliation, and native accounting."""
import json
import os
import sqlite3
from argparse import Namespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from campaigns import capacity_policy as policy
from campaigns import king_hamming as campaign
from matching_solver.artifacts import load_dp
from matching_solver.cluster_solver import write_blocks


def node(name, memory=8 * 1024**3, cpus=8, **extra):
    return dict(node_name=name, memory_bytes=memory, cpu_set=",".join(map(str, range(cpus))),
                state="healthy", compute_enabled=True, **extra)


def large_dp(r=29):
    f = 2 ** ((r - 1) // 2)
    return dict(p=2, r=r, q=2**r, f=f, budget=2*f,
                runs=[dict(a=1, b=1, t=1, repeat=2*f)])


class CapacityCampaignTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**campaign.DEFAULTS, **policy.DEFAULTS}
        self.dp, _ = load_dp(ROOT / "examples/3_3.khdp")
        self.policy = policy.CapacityPolicy()

    def test_native_estimate_matches_one_and_two_thread_kernel(self):
        kernel = ROOT / "matching_solver/kh_match_kernel"
        self.assertTrue(kernel.exists(), "build matching_solver before running these tests")
        for threads in (1, 2, 8):
            if threads > len(os.sched_getaffinity(0)):
                continue
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)
                write_blocks(self.dp, path / "blocks")
                result = subprocess.run([str(kernel), "3", "3", str(path / "blocks"), str(path / "out"),
                                         "--poly", "1,2,0,1", "--threads", str(threads),
                                         "--max-bytes", str((policy.single_memory(self.dp, threads) * 125 + 99) // 100)],
                                        text=True, capture_output=True, timeout=15)
                self.assertIn(result.returncode, (0, 2), result.stderr)
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                summary = next(row for row in rows if "memory_required" in row)
                self.assertEqual(summary["memory_required"], policy.single_memory(self.dp, threads))

    def test_busy_single_host_is_not_reason_to_distribute(self):
        plan = self.policy.plan(self.dp, self.settings, [node("large", allocated=8, reserved_for="other")])
        self.assertTrue(plan["admitted"])
        self.assertEqual((plan["program"], plan["workers"]), ("match", 1))

    def test_largest_machine_preferred(self):
        self.settings["max_matching_bytes"] = 16 * 1024**3
        plan = self.policy.plan(self.dp, self.settings, [node("small"), node("big", 16 * 1024**3)])
        self.assertEqual(plan["hosts"], ["big"])

    def test_reduce_threads_before_splitting(self):
        required = (policy.single_memory(self.dp, 1) * 125 + 99) // 100
        self.settings["max_matching_bytes"] = required
        plan = self.policy.plan(self.dp, self.settings, [node("a"), node("b")])
        self.assertEqual((plan["workers"], plan["threads"]), (1, 1))
        self.assertEqual(plan["max_bytes"], required)

    def test_unknown_memory_and_unhealthy_nodes_are_not_capacity(self):
        unhealthy = node("dead")
        unhealthy["state"] = "lost"
        storage = node("storage")
        storage["compute_enabled"] = False
        plan = self.policy.plan(self.dp, self.settings, [node("unknown", 0), unhealthy, storage])
        self.assertFalse(plan["admitted"])
        self.assertIn("measured healthy", plan["reason"])

    def test_minimum_partitioned_group(self):
        # This setting is isolated test input, never a live limit increase.
        self.settings.update(max_field_elements=2**32-1, matching_threads=1)
        dp = large_dp()
        nodes = [node(str(i), cpus=1) for i in range(9)]
        plan = self.policy.plan(dp, self.settings, nodes)
        self.assertTrue(plan["admitted"])
        self.assertTrue(plan["capacity_feasible"])
        self.assertEqual(plan["program"], "match_partitioned")
        self.assertIn("memory fallback", plan["reason"])
        for fewer in range(2, plan["workers"]):
            memory = max(policy.partitioned_memory(dp, fewer, 1, self.settings["partitioned_batch"]))
            self.assertGreater((memory * 125 + 99) // 100, self.settings["max_matching_bytes"])
        self.assertEqual(len(plan["hosts"]), plan["workers"])

    def test_combined_memory_alone_does_not_admit(self):
        self.settings.update(max_field_elements=2**32-1, matching_workers=2, matching_threads=1)
        nodes = [node("big", 32 * 1024**3), node("tiny", 2 * 1024**3 + 1)]
        plan = self.policy.plan(large_dp(), self.settings, nodes)
        self.assertIn("insufficient per-machine", plan["reason"])

    def test_hard_limits_are_not_bypassed(self):
        self.assertEqual(self.policy.plan(large_dp(), self.settings, [node("a")])["reason"], "field limit")
        self.settings["max_matching_edges"] = 1
        self.assertEqual(self.policy.plan(self.dp, self.settings, [node("a")])["reason"], "edge limit")

    def test_single_host_enqueues_once_and_keeps_pinned_identity(self):
        record = dict(p=3, r=3, q=27, dp_artifact=str(ROOT / "examples/3_3.khdp"), matching_attempts=[])
        pipeline = dict(settings=self.settings, fields={"3^3": record})
        with patch.object(campaign, "request", return_value={"run_id": "match-1", "state": "queued"}) as enqueue:
            self.assertEqual(campaign.advance_matching(Path("unused"), {"leader": "unused"}, {}, [node("a")], pipeline, self.policy),
                             {"submitted": 1})
            campaign.advance_matching(Path("unused"), {"leader": "unused"}, {}, [node("a")], pipeline, self.policy)
        self.assertEqual(enqueue.call_count, 1)
        spec = enqueue.call_args.args[2]["specification"]
        self.assertEqual(spec["program"], "match")
        self.assertIn("dp_sha256", spec["arguments"])
        self.assertTrue(spec["arguments"]["require_known_capacity"])
        from matching_solver.adapter import MatchingAdapter
        self.assertTrue(MatchingAdapter().resource_requirements(spec)["require_known_capacity"])
        self.assertEqual(record["matching_attempts"][0]["plan"]["workers"], 1)

    def test_inadmissible_partitioned_plan_cannot_be_submitted(self):
        with self.assertRaisesRegex(ValueError, "inadmissible"):
            campaign.enqueue_attempt({}, {"settings": self.settings},
                                     {"dp_artifact": str(ROOT / "examples/3_3.khdp")}, [1, 2, 0, 1],
                                     plan={"workers": 2, "admitted": False, "program": "match_partitioned"})

    def test_partitioned_plan_submits_valid_managed_specification(self):
        # Force the fallback for a small fixture; production routing itself is
        # covered with genuinely large inputs above.
        with patch.object(policy, "single_memory", return_value=100 * 1024**3):
            plan = self.policy.plan(self.dp, self.settings, [node("a"), node("b")])
        self.assertEqual((plan["program"], plan["workers"]), ("match_partitioned", 2))
        with patch.object(campaign, "request", return_value={"run_id": "partitioned-1"}) as enqueue:
            campaign.enqueue_attempt({"leader": "unused"}, {"settings": self.settings},
                                     {"dp_artifact": str(ROOT / "examples/3_3.khdp"), "matching_attempts": []},
                                     [1, 2, 0, 1], plan=plan)
        from matching_solver_multi.adapter import PartitionedAdapter
        spec = enqueue.call_args.args[2]["specification"]
        adapter = PartitionedAdapter()
        adapter.validate(spec)
        self.assertEqual(adapter.required_nodes(spec), 2)
        self.assertTrue(adapter.checkpoint_handshake(spec))
        self.assertTrue(adapter.resource_requirements(spec)["require_known_capacity"])

    def test_inadmissible_fields_do_not_block_dp(self):
        record = dict(dp_artifact=str(ROOT / "examples/3_3.khdp"), matching_attempts=[])
        pipeline = dict(settings=self.settings, fields={"3^3": record})
        with patch.object(self.policy, "plan", return_value=dict(admitted=False, reason="insufficient memory")):
            self.assertEqual(campaign.matchable_backlog(pipeline, [node("a")], self.policy), 0)

    def test_collect_completion_even_when_no_current_capacity(self):
        record = dict(p=3, r=3, q=27, dp_artifact=str(ROOT / "examples/3_3.khdp"),
                      matching_attempts=[dict(run_id="done", poly=[1, 2, 0, 1])])
        pipeline = dict(settings=self.settings, fields={"3^3": record})
        runs = {"done": dict(state="complete", progress_done=27, progress_total=27)}
        with patch.object(campaign, "archive_matching", return_value=Path("verified.khmatch")), \
                patch.object(campaign, "request") as enqueue:
            counts = campaign.advance_matching(Path("unused"), {}, runs, [], pipeline, self.policy)
        self.assertTrue(record["matching_complete"])
        self.assertEqual(counts, {"archived": 1, "completed": 1})
        enqueue.assert_not_called()

    def test_legacy_is_not_silently_migrated(self):
        pipeline = {"settings": dict(campaign.DEFAULTS)}
        self.assertIsNone(campaign.matching_policy(pipeline))
        self.assertNotIn("policy", pipeline)
        with self.assertRaises(ValueError):
            campaign.matching_policy({"policy": "typo", "settings": self.settings})

    def test_init_persists_separate_policy_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "manifest.json").write_text('{"leader":"unused","entries":[]}')
            initialized = campaign.initialize(state, Namespace(**self.settings), "capacity")
            self.assertEqual(initialized["policy"], "capacity")
            self.assertIsInstance(campaign.matching_policy(json.loads((state / "pipeline.json").read_text())), policy.CapacityPolicy)
            with self.assertRaisesRegex(ValueError, "already exists"):
                campaign.initialize(state, Namespace(**self.settings), "capacity")

    def test_capacity_entrypoint_refuses_to_run_legacy_pipeline(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            original = json.dumps({"settings": self.settings, "fields": {}})
            (state / "pipeline.json").write_text(original)
            result = subprocess.run([sys.executable, str(ROOT / "campaigns/capacity_campaign.py"),
                                     "--state", str(state), "once"], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn("not a capacity campaign", result.stderr)
            self.assertEqual((state / "pipeline.json").read_text(), original)

    def test_adoption_requires_quiescence_and_preserves_limits_and_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(campaign, "run_rows"):
            state = Path(folder)
            (state / "manifest.json").write_text('{"leader":"unused"}')
            original = dict(settings=dict(campaign.DEFAULTS), fields={"3^3": {"matching_complete": True}})
            (state / "pipeline.json").write_text(json.dumps(original))
            with sqlite3.connect(state / "leader.sqlite") as db:
                db.executescript("CREATE TABLE settings(key TEXT,value TEXT); INSERT INTO settings VALUES('campaign_state','running'); CREATE TABLE runs(state TEXT); INSERT INTO runs VALUES('running');")
            with self.assertRaisesRegex(RuntimeError, "quiesce"):
                campaign.adopt_capacity(state)
            with sqlite3.connect(state / "leader.sqlite") as db:
                db.execute("UPDATE settings SET value='stopped'")
            with self.assertRaisesRegex(RuntimeError, "quiesce"):
                campaign.adopt_capacity(state)
            with sqlite3.connect(state / "leader.sqlite") as db:
                db.execute("UPDATE runs SET state='paused'")
            self.assertTrue(campaign.adopt_capacity(state)["changed"])
            saved = json.loads((state / "pipeline.json").read_text())
            self.assertEqual(saved["fields"], original["fields"])
            for key, value in original["settings"].items():
                self.assertEqual(saved["settings"][key], value)
            self.assertEqual(json.loads((state / "pipeline.before-capacity.json").read_text()), original)
            self.assertFalse(campaign.adopt_capacity(state)["changed"])

    def test_capacity_reconciliation_requires_dispatch_contract(self):
        with patch.object(campaign, "request", return_value={"nodes": [], "runs": []}):
            with self.assertRaisesRegex(RuntimeError, "upgraded leader"):
                campaign.run_rows(Path("unused"), "unused", "known-capacity-admission-v1")
        with patch.object(campaign, "request", return_value={
                "nodes": [], "runs": [], "capabilities": ["known-capacity-admission-v1"]}):
            self.assertEqual(campaign.run_rows(Path("unused"), "unused", "known-capacity-admission-v1"), ({}, []))

    def test_lost_enqueue_reply_does_not_duplicate_after_capacity_change(self):
        source = ROOT / "examples/3_3.khdp"
        _, digest = load_dp(source)
        record = dict(p=3, r=3, q=27, dp_artifact=str(source), matching_attempts=[])
        pipeline = dict(settings=self.settings, fields={"3^3": record})
        # A prior enqueue chose different operational settings; match by exact
        # mathematical input, not the newly calculated job-specification hash.
        runs = {"old": dict(state="running", specification=json.dumps({"program": "match", "arguments": {
            "dp_sha256": digest.hex(), "poly": [1, 2, 0, 1], "threads": 1}}))}
        with patch.object(campaign, "request") as enqueue:
            campaign.advance_matching(Path("unused"), {}, runs, [node("new", cpus=16)], pipeline, self.policy)
        enqueue.assert_not_called()
        self.assertEqual(record["matching_attempts"][0]["run_id"], "old")
        self.assertTrue(record["matching_attempts"][0]["recovered"])

    def test_invalid_planning_settings_rejected(self):
        for key, value in (("memory_margin_percent", 0), ("partitioned_batch", 0), ("matching_workers", 17)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.validate({**self.settings, key: value})


if __name__ == "__main__":
    unittest.main()
