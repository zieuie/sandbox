#!/usr/bin/env python3
"""Check restart-safe continuous DP-to-distributed-matching policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from campaigns import king_hamming as campaign
from matching_solver.artifacts import load_dp

STORAGE_NODES = [{"compute_enabled": True, "state": "healthy",
                  "storage_free_bytes": 100 * 1024**4} for _ in range(3)]


class ContinuousCampaignTests(unittest.TestCase):
    """The feeder preserves exact artifacts, admission bounds, and idempotence."""

    def test_preferred_attempt_uses_durable_nonterminal_frontier(self) -> None:
        """A newer shallow duplicate cannot displace an older durable live attempt."""
        entries = [{"run_id": "durable"}, {"run_id": "newer"}]
        runs = {
            "durable": {"state": "waiting", "_tile_complete": 40,
                        "progress_checkpoint_done": 300, "created": 1},
            "newer": {"state": "running", "_tile_complete": 1,
                      "progress_checkpoint_done": 10, "created": 2},
        }
        entry, run = campaign.preferred_attempt(entries, runs)
        self.assertEqual(entry["run_id"], "durable")
        self.assertEqual(run["_tile_complete"], 40)
        runs["newer"]["state"] = "complete"
        self.assertEqual(campaign.preferred_attempt(entries, runs)[0]["run_id"],
                         "newer")

    def test_collect_and_enqueue_distributed_attempt_once(self) -> None:
        source = ROOT.parent / "examples/3_3.khdp"
        raw = source.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            specification = {"program": "dp_distributed", "arguments": {
                "p": 3, "r": 3, "threads": 1, "tile_side": 4,
                "artifact_format": "KHD1",
            }}
            manifest = {"leader": "http://private", "entries": [{
                "specification": specification, "run_id": "dp-run",
            }]}
            runs = {"dp-run": {"run_id": "dp-run", "state": "complete",
                                "artifact_hash": hashlib.sha256(raw).hexdigest(),
                                "artifact_location": source.resolve().as_uri()}}
            settings = dict(campaign.DEFAULTS)
            settings.update(matching_workers=2, matching_threads=1,
                            minimum_free_bytes=1)
            pipeline = {"settings": settings, "fields": {}}
            self.assertEqual(campaign.collect_dp(state, manifest, runs, pipeline), 1)
            record = pipeline["fields"]["3^3"]
            self.assertEqual(Path(record["dp_artifact"]).read_bytes(), raw)
            submissions = []

            def fake_request(_leader, route, value=None):
                self.assertEqual(route, "/v1/enqueue")
                submissions.append(value)
                return {"run_id": "match-run", "state": "queued", "reused": False}

            nodes = [{"compute_enabled": True}, {"compute_enabled": True}]
            with patch.object(campaign, "request", side_effect=fake_request):
                first = campaign.advance_matching(state, manifest, runs, nodes, pipeline)
                second = campaign.advance_matching(state, manifest, runs, nodes, pipeline)
            self.assertEqual(first, {"submitted": 1})
            self.assertEqual(second, {})
            self.assertEqual(len(submissions), 1)
            self.assertEqual(submissions[0]["specification"]["program"], "match_distributed")
            self.assertEqual(submissions[0]["specification"]["arguments"]["workers"], 2)
            self.assertEqual(submissions[0]["priority"], settings["matching_priority"])

    def test_collection_falls_back_after_worker_port_changes(self) -> None:
        """A rolling worker replacement does not orphan retained DP blobs."""
        source = ROOT.parent / "examples/3_3.khdp"
        raw = source.read_bytes()
        missing = source.with_name("old-port-is-gone.khdp").resolve().as_uri()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            digest = hashlib.sha256(raw).hexdigest()
            storage = state / "new-port"
            (storage / "blobs").mkdir(parents=True)
            (storage / "blobs" / digest).write_bytes(raw)
            specification = {"program": "dp_distributed", "arguments": {
                "p": 3, "r": 3, "threads": 1, "tile_side": 4,
                "artifact_format": "KHD1",
            }}
            manifest = {"entries": [{"specification": specification,
                                      "run_id": "dp-run"}]}
            runs = {"dp-run": {
                "run_id": "dp-run", "state": "complete",
                "artifact_hash": digest,
                "artifact_location": missing,
            }}
            nodes = [{"address": storage.resolve().as_uri()}]
            pipeline = {"settings": dict(campaign.DEFAULTS), "fields": {}}
            self.assertEqual(
                campaign.collect_dp(state, manifest, runs, pipeline, nodes), 1)
            self.assertEqual(Path(pipeline["fields"]["3^3"]["dp_artifact"]).read_bytes(), raw)

    def test_successful_dp_attempt_remains_authoritative_after_later_failure(self) -> None:
        """Artifact provenance and latest attempt state remain separately visible."""

        source = ROOT.parent / "examples/3_3.khdp"
        raw = source.read_bytes()
        specification = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
            "artifact_format": "KHD1",
        }}
        manifest = {"entries": [
            {"specification": specification, "run_id": "successful"},
            {"specification": specification, "run_id": "failed-later"},
        ]}
        runs = {
            "successful": {
                "run_id": "successful", "state": "complete",
                "artifact_hash": hashlib.sha256(raw).hexdigest(),
                "artifact_location": source.resolve().as_uri(),
            },
            "failed-later": {"run_id": "failed-later", "state": "failed"},
        }
        pipeline = {"settings": dict(campaign.DEFAULTS), "fields": {}}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(campaign.collect_dp(
                Path(directory), manifest, runs, pipeline), 1)
        record = pipeline["fields"]["3^3"]
        self.assertEqual(record["dp_run_id"], "successful")
        self.assertEqual(record["dp_state"], "complete")
        self.assertEqual(record["dp_current_run_id"], "failed-later")
        self.assertEqual(record["dp_current_state"], "failed")
        self.assertEqual([item["state"] for item in record["dp_attempts"]],
                         ["complete", "failed"])

    def test_failed_matching_attempt_retries_same_candidate_then_stops(self) -> None:
        """Engine failures rerun the same polynomial with a bounded attempt count."""

        source = ROOT.parent / "matching_solver/examples/13_5.khdp"
        settings = dict(campaign.DEFAULTS)
        settings.update(matching_workers=2, matching_threads=1,
                        matching_retry_seconds=1, max_matching_attempts=2)
        polynomial = [2, 4, 0, 0, 0, 1]
        record = {"p": 13, "r": 5, "dp_artifact": str(source),
                  "matching_attempts": [{"run_id": "failed-1",
                                          "poly": polynomial}]}
        pipeline = {"settings": settings, "fields": {"13^5": record}}
        manifest = {"leader": "http://private", "entries": []}
        nodes = [{"compute_enabled": True, "state": "healthy"} for _ in range(2)]
        runs = {"failed-1": {"run_id": "failed-1", "state": "failed",
                              "finished": 1, "error": "transient"}}
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": "failed-2", "state": "queued", "reused": False}

        with patch.object(campaign, "request", side_effect=fake_request):
            self.assertEqual(campaign.advance_matching(
                Path("unused"), manifest, runs, nodes, pipeline), {"retried": 1})
        self.assertEqual(submissions[0]["rerun"], True)
        self.assertEqual(record["matching_attempts"][-1]["poly"], polynomial)

        runs["failed-2"] = {"run_id": "failed-2", "state": "failed",
                             "finished": 1, "error": "still broken"}
        with patch.object(campaign, "request") as submitted:
            self.assertEqual(campaign.advance_matching(
                Path("unused"), manifest, runs, nodes, pipeline),
                {"failed_terminal": 1})
        submitted.assert_not_called()
        self.assertEqual(record["matching_failure"], "still broken")

    def test_an_attempt_stopped_for_a_defect_does_not_count(self) -> None:
        """A run cancelled to fix a defect (not_counted) is retried without using up an attempt."""

        source = ROOT.parent / "matching_solver/examples/13_5.khdp"
        settings = dict(campaign.DEFAULTS)
        settings.update(matching_workers=2, matching_threads=1,
                        matching_retry_seconds=1, max_matching_attempts=2)
        polynomial = [2, 4, 0, 0, 0, 1]
        record = {"p": 13, "r": 5, "dp_artifact": str(source),
                  "matching_attempts": [{"run_id": "misplaced", "poly": polynomial, "not_counted": "defect"},
                                        {"run_id": "failed-1", "poly": polynomial}]}
        pipeline = {"settings": settings, "fields": {"13^5": record}}
        manifest = {"leader": "http://private", "entries": []}
        nodes = [{"compute_enabled": True, "state": "healthy"} for _ in range(2)]
        runs = {"misplaced": {"run_id": "misplaced", "state": "cancelled", "finished": 1},
                "failed-1": {"run_id": "failed-1", "state": "cancelled", "finished": 1}}
        with patch.object(campaign, "request", return_value={"run_id": "again", "state": "queued", "reused": False}):
            self.assertEqual(campaign.advance_matching(
                Path("unused"), manifest, runs, nodes, pipeline), {"retried": 1})
        self.assertNotIn("matching_failure", record)

    def test_incomplete_wide_matching_moves_to_the_next_polynomial(self) -> None:
        self.test_incomplete_block_matching_moves_to_the_next_polynomial(
            "gpu_wide_match_solver/cluster_solver.py: wide matching incomplete: 7 requests unmatched after 31 rounds")

    def test_incomplete_block_matching_moves_to_the_next_polynomial(self, message: str | None = None) -> None:
        """An incomplete block run is deterministic: try another polynomial, and stop after the limit."""

        source = ROOT.parent / "matching_solver/examples/13_5.khdp"
        settings = dict(campaign.DEFAULTS)
        settings.update(matching_workers=2, matching_threads=1, matching_retry_seconds=1, max_matching_attempts=2)
        polynomial = [2, 4, 0, 0, 0, 1]
        record = {"p": 13, "r": 5, "q": 13**5, "dp_artifact": str(source),
                  "matching_attempts": [{"run_id": "short-1", "poly": polynomial}]}
        pipeline = {"settings": settings, "fields": {"13^5": record}}
        manifest = {"leader": "http://private", "entries": []}
        nodes = [{"compute_enabled": True, "state": "healthy"} for _ in range(2)]
        message = message or "gpu_block_match_solver/cluster_solver.py: block matching incomplete: 7 requests unmatched"
        runs = {"short-1": {"run_id": "short-1", "state": "failed", "finished": 1, "error": message}}
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": "short-2", "state": "queued", "reused": False}

        with patch.object(campaign, "request", side_effect=fake_request):
            self.assertEqual(campaign.advance_matching(Path("unused"), manifest, runs, nodes, pipeline), {"submitted": 1})
        self.assertNotIn("rerun", submissions[0])
        self.assertNotEqual(record["matching_attempts"][-1]["poly"], polynomial)
        self.assertTrue(record["matching_attempts"][0]["incomplete"])

        runs["short-2"] = {"run_id": "short-2", "state": "failed", "finished": 1, "error": message}
        with patch.object(campaign, "request") as submitted:
            self.assertEqual(campaign.advance_matching(Path("unused"), manifest, runs, nodes, pipeline), {"failed_terminal": 1})
        submitted.assert_not_called()

    def test_native_memory_admission_matches_documented_frontier(self) -> None:
        dp, _ = load_dp(ROOT.parent / "matching_solver/examples/13_5.khdp")
        coordinator, shard = campaign.distributed_memory_required(dp)
        self.assertGreater(coordinator, 0)
        self.assertGreater(shard, 0)
        settings = dict(campaign.DEFAULTS)
        admitted, reason = campaign.matching_admitted(dp, settings)
        self.assertTrue(admitted, reason)
        settings["max_matching_bytes"] = max(coordinator, shard) - 1
        self.assertEqual(campaign.matching_admitted(dp, settings),
                         (False, "memory limit"))

    def test_matching_group_tiers_use_small_medium_and_full_groups(self) -> None:
        settings = dict(campaign.DEFAULTS)
        small = {"q": 8, "f": 1, "runs": [{"a": 1, "t": 1, "repeat": 8}]}
        medium = {"q": 8, "f": 1, "runs": [{"a": 1, "t": 1,
                                                  "repeat": 2_000_000}]}
        large = {"q": 8, "f": 1, "runs": [{"a": 1, "t": 1,
                                                 "repeat": 20_000_000}]}
        self.assertEqual(campaign.matching_worker_count(small, settings), 2)
        self.assertEqual(campaign.matching_worker_count(medium, settings), 4)
        self.assertEqual(campaign.matching_worker_count(large, settings), 9)

    def test_compact_replicated_budget_admits_two_to_the_twenty_ninth(self) -> None:
        """Bounded replies and compact state bring 2^29 below 12 GiB per process."""
        dp = {
            "q": 2**29,
            "f": 2**14,
            "runs": [
                {"a": 1, "t": 1, "repeat": 2},
                {"a": 1, "t": 1, "repeat": 10922},
                {"a": 2, "t": 1, "repeat": 10922},
            ],
        }
        coordinator, shard = campaign.distributed_memory_required(dp, 9, 16)
        self.assertLess(coordinator, 12 * 1024**3)
        self.assertLess(shard, 12 * 1024**3)
        self.assertGreater(coordinator, 11 * 1024**3)
        self.assertGreater(shard, 11 * 1024**3)

    def test_inadmissible_matching_does_not_stop_dp_frontier(self) -> None:
        """Retained DP remains useful even when this cluster cannot match it yet."""
        source = ROOT.parent / "matching_solver/examples/13_5.khdp"
        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_ready_fields=1,
                        max_field_elements=1, minimum_free_bytes=1)
        pipeline = {"settings": settings, "fields": {
            "13^5": {"p": 13, "r": 5, "dp_artifact": str(source),
                     "matching_attempts": []},
        }}
        candidate = {"program": "dp_distributed", "arguments": {
            "p": 17, "r": 3, "threads": 1, "tile_side": 4,
        }}
        manifest = {"leader": "http://private", "entries": []}
        submissions = []

        def fake_request(_leader, route, value=None):
            self.assertEqual(route, "/v1/enqueue")
            submissions.append(value)
            return {"run_id": "new-dp", "state": "queued", "reused": False}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter([candidate])), \
                patch.object(campaign, "request", side_effect=fake_request):
            added = campaign.replenish_dp(
                Path(directory), manifest, {}, pipeline, STORAGE_NODES)

        self.assertEqual(added, 1)
        self.assertEqual(pipeline["fields"]["13^5"]["matching_admission"],
                         "field limit")
        self.assertEqual(submissions[0]["specification"]["program"],
                         "dp_distributed")

    def test_replenish_does_not_exceed_an_already_full_root_budget(self) -> None:
        """An active root consumes the only slot without enqueueing a candidate."""

        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_ready_fields=4,
                        minimum_free_bytes=1)
        existing = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
        }}
        candidate = {"program": "dp", "arguments": {
            "p": 5, "r": 3, "threads": 1, "tile_side": 4,
        }}
        manifest = {"leader": "http://private", "entries": [{
            "run_id": "active", "specification": existing,
        }]}
        pipeline = {"settings": settings, "fields": {}}
        runs = {"active": {"run_id": "active", "state": "waiting"}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter([candidate])), \
                patch.object(campaign, "request") as submitted:
            added = campaign.replenish_dp(
                Path(directory), manifest, runs, pipeline)
        self.assertEqual(added, 0)
        submitted.assert_not_called()

    def test_feeder_retries_timed_out_reconstruction_without_new_attempt(self) -> None:
        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_dp_roots=1, minimum_free_bytes=1)
        specification = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
        }}
        manifest = {"leader": "http://private", "entries": [
            {"run_id": "failed-root", "specification": specification},
        ]}
        runs = {"failed-root": {"run_id": "failed-root", "state": "failed",
                                "error": "distributed_solver.py: timed out"}}
        pipeline = {"settings": settings, "fields": {"3^3": {"p": 3, "r": 3}}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign, "request", return_value={"state": "queued"}) as sent:
            added = campaign.replenish_dp(Path(directory), manifest, runs, pipeline)
        self.assertEqual(added, 1)
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(pipeline["fields"]["3^3"]["dp_reconstruction_retries"],
                         {"failed-root": 1})
        sent.assert_called_once_with("http://private", "/v1/run-command", {
            "run_id": "failed-root", "action": "retry-reconstruction",
        })

    def test_retry_filling_root_budget_does_not_add_a_new_field(self) -> None:
        """A failed-root retry and a new candidate share one exact capacity budget."""

        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_ready_fields=4,
                        minimum_free_bytes=1)
        failed = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
        }}
        candidate = {"program": "dp", "arguments": {
            "p": 5, "r": 3, "threads": 1, "tile_side": 4,
        }}
        manifest = {"leader": "http://private", "entries": [{
            "run_id": "failed", "specification": failed,
        }]}
        pipeline = {"settings": settings, "fields": {
            "3^3": {"p": 3, "r": 3, "dp_state": "failed"},
        }}
        runs = {"failed": {"run_id": "failed", "state": "failed"}}
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": "retry", "state": "waiting", "reused": False}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter([candidate])), \
                patch.object(campaign, "request", side_effect=fake_request):
            added = campaign.replenish_dp(
                Path(directory), manifest, runs, pipeline)
        self.assertEqual(added, 1)
        self.assertEqual(len(submissions), 1)
        self.assertTrue(submissions[0]["rerun"])
        self.assertEqual(submissions[0]["specification"]["arguments"]["p"], 3)

    def test_new_dp_fields_off_starts_no_new_field_but_still_retries(self) -> None:
        """Wrapping up: no frontier candidate is submitted, but a submitted field's retry still is."""

        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=2, max_ready_fields=4, minimum_free_bytes=1, new_dp_fields=0)
        failed = {"program": "dp_distributed", "arguments": {"p": 3, "r": 3, "threads": 1, "tile_side": 4}}
        candidate = {"program": "dp", "arguments": {"p": 5, "r": 3, "threads": 1, "tile_side": 4}}
        manifest = {"leader": "http://private", "entries": [{"run_id": "failed", "specification": failed}]}
        pipeline = {"settings": settings, "fields": {"3^3": {"p": 3, "r": 3, "dp_state": "failed"}}}
        runs = {"failed": {"run_id": "failed", "state": "failed"}}
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": "retry", "state": "waiting", "reused": False}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter([candidate])) as frontier, \
                patch.object(campaign, "request", side_effect=fake_request):
            added = campaign.replenish_dp(Path(directory), manifest, runs, pipeline, STORAGE_NODES)
        self.assertEqual(added, 1)
        self.assertEqual([item["specification"]["arguments"]["p"] for item in submissions], [3])
        frontier.assert_not_called()
        self.assertFalse(pipeline["demand"]["new_dp_fields"])
        with self.assertRaises(ValueError):
            campaign.validate_settings({**campaign.DEFAULTS, "new_dp_fields": 2})

    def test_shallow_ready_tile_pool_admits_independent_roots_to_hard_cap(self) -> None:
        """Dependency stalls are backfilled by other roots without an unlimited backlog."""

        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_dp_roots=4,
                        target_ready_dp_tiles=8, max_ready_fields=4,
                        minimum_free_bytes=1)
        existing = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
        }}
        candidates = [{"program": "dp", "arguments": {
            "p": prime, "r": 3, "threads": 1, "tile_side": 4,
        }} for prime in (5, 7, 11, 13, 17)]
        manifest = {"leader": "http://private", "entries": [{
            "run_id": "active", "specification": existing,
        }]}
        pipeline = {"settings": settings, "fields": {}}
        runs = {"active": {"run_id": "active", "state": "waiting",
                           "_tile_metrics": True, "_ready_tiles": 0}}
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": f"new-{len(submissions)}", "state": "waiting",
                    "reused": False}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter(candidates)), \
                patch.object(campaign, "request", side_effect=fake_request):
            added = campaign.replenish_dp(
                Path(directory), manifest, runs, pipeline, STORAGE_NODES)
        self.assertEqual(added, 3)
        self.assertEqual(len(submissions), 3)

    def test_added_nodes_raise_the_measured_ready_tile_target(self) -> None:
        """Root demand follows healthy compute capacity while respecting the hard root cap."""
        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, max_dp_roots=6,
                        target_ready_dp_tiles=2, max_ready_fields=10,
                        minimum_free_bytes=1)
        existing = {"program": "dp_distributed", "arguments": {
            "p": 3, "r": 3, "threads": 1, "tile_side": 4,
        }}
        candidates = [{"program": "dp", "arguments": {
            "p": prime, "r": 3, "threads": 1, "tile_side": 4,
        }} for prime in (5, 7, 11, 13, 17, 19)]
        manifest = {"leader": "http://private", "entries": [{
            "run_id": "active", "specification": existing,
        }]}
        pipeline = {"settings": settings, "fields": {}}
        runs = {"active": {"run_id": "active", "state": "waiting",
                           "_tile_metrics": True, "_ready_tiles": 1}}
        nodes = [{"compute_enabled": True, "state": "healthy",
                  "storage_free_bytes": 100 * 1024**4} for _ in range(4)]
        submissions = []

        def fake_request(_leader, route, value=None):
            submissions.append(value)
            return {"run_id": f"new-{len(submissions)}", "state": "waiting",
                    "reused": False}

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter(candidates)), \
                patch.object(campaign, "request", side_effect=fake_request):
            added = campaign.replenish_dp(
                Path(directory), manifest, runs, pipeline, nodes)
        self.assertEqual(added, 5)
        self.assertEqual(pipeline["demand"]["ready_tile_target"], 8)
        self.assertEqual(pipeline["demand"]["live_compute_nodes"], 4)

    def test_regional_frontier_prefers_table_shape_and_fitting_tiles(self) -> None:
        from dp_solver.scheduling import regional_campaign, dp_estimate
        candidates = list(regional_campaign(19, 11, 200_000_000_000_000, 16, 2 * 1024**3))
        positions = {(job["arguments"]["p"], job["arguments"]["r"]): index
                     for index, job in enumerate(candidates)}
        self.assertLess(positions[(11, 9)], positions[(17, 7)])
        self.assertLess(positions[(17, 7)], positions[(19, 7)])
        self.assertGreater(dp_estimate(candidates[positions[(11, 9)]])["state_bytes"], 16 * 1024**3)
        self.assertEqual(candidates[positions[(11, 9)]]["program"], "dp_distributed")
        self.assertGreaterEqual(candidates[positions[(11, 9)]]["arguments"]["tile_side"], 2048)

    def test_regional_frontier_waits_for_measured_worker_disk(self) -> None:
        settings = dict(campaign.DEFAULTS)
        settings.update(target_dp_roots=1, minimum_free_bytes=1)
        pipeline = {"settings": settings, "fields": {}}
        manifest = {"leader": "http://private", "entries": []}
        candidate = {"program": "dp_distributed", "arguments": {
            "p": 11, "r": 9, "threads": 16, "tile_side": 2048}}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(campaign.scheduling, "regional_campaign", return_value=iter([candidate])), \
                patch.object(campaign, "request") as enqueue:
            self.assertEqual(campaign.replenish_dp(
                Path(directory), manifest, {}, pipeline,
                [{"compute_enabled": True, "state": "healthy"} for _ in range(3)]), 0)
        enqueue.assert_not_called()

    def test_settings_reject_an_unreservable_thread_product(self) -> None:
        settings = dict(campaign.DEFAULTS)
        settings.update(matching_workers=9, matching_threads=29)
        with self.assertRaisesRegex(ValueError, "thread product"):
            campaign.validate_settings(settings)


if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test continuous campaign policy. Example: python3 test_continuous_campaign.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
