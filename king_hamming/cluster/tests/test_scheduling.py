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

        for p, r in ((2, 3), (3, 5), (5, 3), (13, 5), (2, 31), (3, 21), (1621, 3)):
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

        for p, r in ((4, 3), (3, 4), (3, 32), (True, 3)):
            with self.assertRaises(ValueError):
                scheduling.dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})
        with self.assertRaises(ValueError):
            list(scheduling.campaign(max_visits=2**64))
        with self.assertRaises(ValueError):
            adapters.estimate_seconds({"program": "demo"}, float("nan"))
        self.assertEqual(list(scheduling.campaign(max_visits=1)), [])

    def test_regional_frontier_admits_q_above_uint32_when_tiles_fit(self) -> None:
        """Large dense state may still have a bounded distributed tile layout."""

        entries = scheduling.regional_campaign(
            max_prime=3, max_exponent=21, max_visits=10**12,
            threads=8, max_tile_bytes=2 * 1024**3,
        )
        specification = next(
            item for item in entries
            if (item["arguments"]["p"], item["arguments"]["r"]) == (3, 21)
        )
        self.assertEqual(specification["arguments"]["tile_side"], 2048)
        self.assertGreater(
            scheduling.dp_estimate(specification)["q"], scheduling.UINT32_MAX,
        )


# Exercise actual dispatch transactions rather than mirroring the SQL sort expression.
class QueueTests(unittest.TestCase):
    """Manual priorities override runtime order while duplicate and rerun history remain intact."""

    def test_disjoint_slots_run_tiles_concurrently_but_block_exclusive_work(self) -> None:
        """Two immutable tiles share a host; a whole-host root cannot overlap them."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            handler.dispatch_post("/v1/register", {
                "node_name": "slotted", "cpu_set": "0,1,2",
                "memory_bytes": 10 * 1024**3,
                "slots": [{"slot_id": index, "cpu_set": str(index)} for index in range(3)],
            })
            for p in (3, 5):
                handler.dispatch_post("/v1/enqueue", {"specification": {
                    "program": "dp_distributed", "arguments": {
                        "p": p, "r": 3, "tile_side": 4, "threads": 3, "max_cpus": 1,
                        "max_visits": 10**9, "max_tile_bytes": 2 * 1024**3,
                    },
                }})
            first = handler.dispatch_post("/v1/lease", {
                "node_name": "slotted", "slot_id": 0})["job"]
            second = handler.dispatch_post("/v1/lease", {
                "node_name": "slotted", "slot_id": 1})["job"]
            self.assertEqual(first["specification"]["program"], "dp_tile")
            self.assertEqual(second["specification"]["program"], "dp_tile")
            self.assertEqual(first["assigned_cpu_set"], "0")
            self.assertEqual(second["assigned_cpu_set"], "1")
            handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp", "arguments": {"p": 3, "r": 3},
            }})
            self.assertIsNone(handler.dispatch_post("/v1/lease", {
                "node_name": "slotted", "slot_id": 2})["job"])

    def test_reconstruction_drains_one_host_before_more_tiles(self) -> None:
        """Ready reconstruction outranks tiles, without idling other hosts."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            for name in ("a", "b"):
                handler.dispatch_post("/v1/register", {
                    "node_name": name, "cpu_set": "0,1",
                    "memory_bytes": 10 * 1024**3,
                    "slots": [{"slot_id": index, "cpu_set": str(index)}
                              for index in range(2)],
                })
            roots = [handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": p, "r": 3, "tile_side": 4, "threads": 1, "max_cpus": 1,
                    "max_tile_bytes": 2 * 1024**3,
                },
            }})["run_id"] for p in (3, 5, 7)]
            first = handler.dispatch_post("/v1/lease", {
                "node_name": "a", "slot_id": 0})["job"]
            second = handler.dispatch_post("/v1/lease", {
                "node_name": "b", "slot_id": 0})["job"]
            self.assertEqual(first["specification"]["program"], "dp_tile")
            self.assertEqual(second["specification"]["program"], "dp_tile")
            with leader.connect(database) as connection:
                connection.execute(
                    "UPDATE runs SET state='queued',progress_phase='reconstructing' "
                    "WHERE run_id=?", (roots[0],))
                connection.execute(
                    "UPDATE runs SET priority=100 WHERE parent_run_id=? AND state='queued'",
                    (roots[2],))
            # Host a drains for reconstruction; host b continues high-priority tiles.
            self.assertIsNone(handler.dispatch_post("/v1/lease", {
                "node_name": "a", "slot_id": 1})["job"])
            other = handler.dispatch_post("/v1/lease", {
                "node_name": "b", "slot_id": 1})["job"]
            self.assertEqual(other["specification"]["program"], "dp_tile")
            handler.dispatch_post("/v1/requeue", {
                "run_id": first["run_id"], "lease_token": first["lease_token"]})
            reconstruction = handler.dispatch_post("/v1/lease", {
                "node_name": "a", "slot_id": 1})["job"]
            self.assertEqual(reconstruction["run_id"], roots[0])
            self.assertEqual(reconstruction["specification"]["program"], "dp_distributed")

    def test_tile_leases_use_disjoint_small_teams_with_memory_safe_fallback(self) -> None:
        """Old one-thread specs get two CPUs by default, leaving another team free."""
        from dp_solver.tiles import tile, memory_bytes
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            handler.dispatch_post("/v1/register", {
                "node_name": "elastic", "cpu_set": "1,3,5,7",
                "memory_bytes": 10 * 1024**3,
                "slots": [{"slot_id": i, "cpu_set": str(cpu)}
                          for i, cpu in enumerate((1, 3, 5, 7))],
            })
            for p in (3, 5):
                handler.dispatch_post("/v1/enqueue", {"specification": {
                    "program": "dp_distributed", "arguments": {
                        "p": p, "r": 3, "tile_side": 4, "threads": 1,
                    }}})
            first = handler.dispatch_post("/v1/lease", {
                "node_name": "elastic", "slot_id": 2})["job"]
            self.assertEqual(first["assigned_cpu_set"], "1,3")
            adapter = adapters.get(first["specification"])
            local = adapter.worker_specification(first["specification"], [1, 3])
            self.assertEqual(local["arguments"]["threads"], 2)
            self.assertEqual(first["specification"]["arguments"]["threads"], 1)
            concurrent = handler.dispatch_post("/v1/lease", {
                "node_name": "elastic", "slot_id": 0})["job"]
            self.assertEqual(concurrent["assigned_cpu_set"], "5,7")
            self.assertIsNone(handler.dispatch_post("/v1/lease", {
                "node_name": "elastic", "slot_id": 1})["job"])
            # Stop/release fences every CPU in the team, not only its slot ID.
            handler.dispatch_post("/v1/requeue", {
                "run_id": first["run_id"], "lease_token": first["lease_token"]})
            with leader.connect(database) as connection:
                active = connection.execute(
                    "SELECT assigned_cpu_set FROM runs WHERE node_name='elastic' AND state='running'"
                ).fetchall()
            self.assertEqual([row[0] for row in active], ["5,7"])
            spec = first["specification"]
            args = spec["arguments"]
            target = tile(args["p"], args["r"], args["tile_side"], 0, 0)
            args["max_tile_bytes"] = memory_bytes(args["p"], target, 2)
            self.assertEqual(adapter.cpu_width(spec, 14), 2)
            self.assertEqual(adapter.cpu_width(spec, 0), 0)
            args["max_tile_bytes"] -= 1
            self.assertEqual(adapter.cpu_width(spec, 14), 1)

    def test_ready_tile_roots_share_leases_before_shorter_root_repeats(self) -> None:
        """A queued tile from an inactive root outranks another cheap tile."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            handler.dispatch_post("/v1/register", {
                "node_name": "fair", "cpu_set": "0,1,2",
                "memory_bytes": 10 * 1024**3,
                "slots": [{"slot_id": cpu, "cpu_set": str(cpu)} for cpu in range(3)],
            })
            roots = [handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": p, "r": 3, "tile_side": 4, "max_cpus": 1,
                },
            }})["run_id"] for p in (3, 5)]
            with leader.connect(database) as connection:
                connection.execute(
                    "INSERT INTO runs(run_id,calculation_id,specification,state,priority,"
                    "from_scratch,created,estimated_seconds,parent_run_id) "
                    "SELECT 'extra-cheap',calculation_id,specification,state,priority,"
                    "from_scratch,created,estimated_seconds,parent_run_id FROM runs "
                    "WHERE parent_run_id=? AND state='queued' LIMIT 1", (roots[0],),
                )
            first = handler.dispatch_post("/v1/lease", {
                "node_name": "fair", "slot_id": 0})["job"]
            second = handler.dispatch_post("/v1/lease", {
                "node_name": "fair", "slot_id": 1})["job"]
            self.assertEqual(first["specification"]["arguments"]["parent_run_id"], roots[0])
            self.assertEqual(second["specification"]["arguments"]["parent_run_id"], roots[1])

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
                samples = connection.execute(
                    "SELECT COUNT(*) FROM resource_usage_samples WHERE run_id=?",
                    (queued["run_id"],)).fetchone()[0]
            self.assertEqual(tuple(usage[0]), ("match-2", "coordinator", -1, 100, 200))
            self.assertEqual(tuple(usage[1]), ("match-1", "shard", 2, 300, 400))
            self.assertEqual(samples, 3)
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


    def test_run_scoped_pause_resume_cancel_and_priority(self) -> None:
        """Control one run without stopping dispatch or changing unrelated work."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            handler.dispatch_post("/v1/register", {"node_name": "worker"})
            first = handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}, "rerun": True})
            second = handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}, "rerun": True})
            self.assertEqual(handler.dispatch_post("/v1/run-command", {
                "run_id": first["run_id"], "action": "pause",
            })["state"], "paused")
            handler.dispatch_post("/v1/run-command", {
                "run_id": first["run_id"], "action": "reprioritize", "priority": 7,
            })
            leased = handler.dispatch_post("/v1/lease", {"node_name": "worker"})["job"]
            self.assertEqual(leased["run_id"], second["run_id"] )
            identity = {"run_id": second["run_id"], "lease_token": leased["lease_token"]}
            result = handler.dispatch_post("/v1/run-command", {
                "run_id": second["run_id"], "action": "pause",
            })
            self.assertEqual(result["target_state"], "paused")
            self.assertTrue(handler.dispatch_post("/v1/run-control", identity)["stop_requested"] )
            handler.dispatch_post("/v1/requeue", identity)
            handler.dispatch_post("/v1/run-command", {"run_id": second["run_id"], "action": "resume"})
            handler.dispatch_post("/v1/run-command", {"run_id": second["run_id"], "action": "cancel"})
            handler.dispatch_post("/v1/run-command", {"run_id": first["run_id"], "action": "resume"})
            resumed = handler.dispatch_post("/v1/lease", {"node_name": "worker"})["job"]
            self.assertEqual(resumed["run_id"], first["run_id"] )
            with leader.connect(database) as connection:
                row = connection.execute("SELECT state,priority FROM runs WHERE run_id=?", (second["run_id"],)).fetchone()
                self.assertEqual((row["state"], row["priority"]), ("cancelled", 0))

    def test_distributed_root_resume_returns_to_dependency_scheduler(self) -> None:
        """A partially materialized tile DAG cannot lease reconstruction on resume."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            specification = {"program": "dp_distributed", "arguments": {
                "p": 5, "r": 3, "tile_side": 7, "threads": 1,
                "artifact_format": "KHD1",
            }}
            queued = handler.dispatch_post(
                "/v1/enqueue", {"specification": specification})
            self.assertEqual(queued["state"], "waiting")
            self.assertEqual(handler.dispatch_post("/v1/run-command", {
                "run_id": queued["run_id"], "action": "pause",
            })["state"], "paused")
            resumed = handler.dispatch_post("/v1/run-command", {
                "run_id": queued["run_id"], "action": "resume",
            })
            self.assertEqual(resumed["state"], "waiting")
            with leader.connect(database) as connection:
                row = connection.execute(
                    "SELECT state,progress_phase,progress_message FROM runs WHERE run_id=?",
                    (queued["run_id"],),
                ).fetchone()
                self.assertEqual(tuple(row), ("waiting", "tiles", "resumed"))

            # A worker may lease ready tile children, never the parent reconstruction.
            handler.dispatch_post("/v1/register", {"node_name": "worker"})
            leased = handler.dispatch_post(
                "/v1/lease", {"node_name": "worker"})["job"]
            self.assertNotEqual(leased["run_id"], queued["run_id"])
            self.assertEqual(leased["specification"]["program"], "dp_tile")

    def test_startup_repairs_a_legacy_queued_distributed_root(self) -> None:
        """An already-misclassified partial root is reconciled before it can lease."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            queued = handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": 5, "r": 3, "tile_side": 7, "threads": 1,
                    "artifact_format": "KHD1",
                },
            }})
            with leader.connect(database) as connection:
                connection.execute(
                    "UPDATE runs SET state='queued',progress_phase='queued' WHERE run_id=?",
                    (queued["run_id"],),
                )
            leader.initialize(database, 1800)
            with leader.connect(database) as connection:
                row = connection.execute(
                    "SELECT state,progress_phase,progress_message FROM runs WHERE run_id=?",
                    (queued["run_id"],),
                ).fetchone()
                self.assertEqual(tuple(row), (
                    "waiting", "tiles", "reconciling durable tile frontier"))

    def test_startup_repairs_stop_induced_tile_failure(self) -> None:
        """Retain completed frontier links and retry a child misfailed during stop."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            queued = handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": 5, "r": 3, "tile_side": 7, "threads": 1,
                    "artifact_format": "KHD1",
                },
            }})
            with leader.connect(database) as connection:
                child = connection.execute(
                    "SELECT child_run_id FROM distributed_tiles "
                    "WHERE parent_run_id=? AND child_run_id IS NOT NULL LIMIT 1",
                    (queued["run_id"],)).fetchone()[0]
                error = "solver failed while stopping (exit 1): C tile kernel exited -15"
                connection.execute(
                    "UPDATE runs SET state='failed',error=?,failure_kind='stop_failure' "
                    "WHERE run_id=?", (error, child))
                connection.execute(
                    "UPDATE runs SET state='failed',error=?,finished=1 WHERE run_id=?",
                    (f"tile 0,0: {error}", queued["run_id"]))

            leader.initialize(database, 1800)
            with leader.connect(database) as connection:
                parent = connection.execute(
                    "SELECT state,error,finished,progress_phase,progress_message "
                    "FROM runs WHERE run_id=?", (queued["run_id"],)).fetchone()
                linked = connection.execute(
                    "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=?",
                    (queued["run_id"],)).fetchall()
                self.assertEqual(tuple(parent), (
                    "waiting", None, None, "tiles",
                    "recovering interrupted tile frontier"))
                self.assertNotIn(child, [row[0] for row in linked])

    def test_nine_node_heterogeneous_matching_resources(self) -> None:
        """Admit nine nodes and pass each shard its actual allocation up to the requested cap."""

        from matching_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            for index in range(9):
                cpus = ",".join(map(str, range(12))) if index == 0 else "0,1"
                handler.dispatch_post("/v1/register", {
                    "node_name": f"node-{index}", "address": f"http://127.0.0.1:{9100 + index}",
                    "cpu_set": cpus,
                })
            job_spec = specification(ROOT.parent / "examples/3_3.khdp",
                                     "1,2,0,1", 12, 2**31, distributed=True, workers=9)
            queued = handler.dispatch_post("/v1/enqueue", {"specification": job_spec})
            job = handler.dispatch_post("/v1/lease", {"node_name": "node-8"})["job"]
            self.assertEqual(job["run_id"], queued["run_id"] )
            adapter = adapters.get(job_spec)
            local = adapter.worker_specification(job_spec, [0, 1])
            extra = adapter.prepare(local, root, job, "unused")
            counts = extra[extra.index("--worker-threads") + 1]
            self.assertEqual(counts, "2,12,2,2,2,2,2,2,2")

    def test_matching_coordinator_placement_respects_aggregate_memory(self) -> None:
        """A coordinator plus local shard cannot land on an undersized host."""

        from matching_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            for name, memory in (("small", 1), ("large", 16 * 1024**3),
                                 ("partner", 16 * 1024**3)):
                handler.dispatch_post("/v1/register", {
                    "node_name": name, "address": "http://127.0.0.1:1",
                    "cpu_set": "0", "memory_bytes": memory,
                })
            job_spec = specification(ROOT.parent / "examples/3_3.khdp",
                                     "1,2,0,1", 1, 2**31,
                                     distributed=True, workers=2)
            queued = handler.dispatch_post(
                "/v1/enqueue", {"specification": job_spec})
            self.assertIsNone(handler.dispatch_post(
                "/v1/lease", {"node_name": "small"})["job"])
            leased = handler.dispatch_post(
                "/v1/lease", {"node_name": "large"})["job"]
            self.assertEqual(leased["run_id"], queued["run_id"])
            self.assertEqual(leased["node_name"], "large")

    def test_adapter_may_reserve_more_than_nine_nodes(self) -> None:
        """The generic scheduler follows adapter/native limits rather than a fleet-size constant."""

        from matching_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            for index in range(10):
                handler.dispatch_post("/v1/register", {
                    "node_name": f"node-{index:02d}", "cpu_set": "0",
                    "address": f"http://127.0.0.1:{9300 + index}",
                })
            job_spec = specification(ROOT.parent / "examples/3_3.khdp",
                                     "1,2,0,1", 1, 2**31,
                                     distributed=True, workers=10)
            queued = handler.dispatch_post(
                "/v1/enqueue", {"specification": job_spec})
            job = handler.dispatch_post(
                "/v1/lease", {"node_name": "node-00"})["job"]
            self.assertEqual(job["run_id"], queued["run_id"])
            self.assertEqual(len(job["reserved_workers"]), 9)
    def test_priority_group_waits_for_busy_nodes_instead_of_starving(self) -> None:
        """Idle nodes coalesce behind a high-priority group before taking more DP work."""
        from matching_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            for index in range(4):
                handler.dispatch_post("/v1/register", {
                    "node_name": f"node-{index}",
                    "address": f"http://127.0.0.1:{9200 + index}", "cpu_set": "0",
                })
            occupied = []
            for index in range(3):
                queued = handler.dispatch_post("/v1/enqueue", {
                    "specification": {"program": "dp", "arguments": {"p": 3, "r": 3}},
                    "rerun": True,
                })
                occupied.append(queued["run_id"])
                handler.dispatch_post("/v1/lease", {"node_name": f"node-{index}"})
            group = specification(ROOT.parent / "examples/3_3.khdp", "1,2,0,1",
                                  1, 2**31, distributed=True, workers=4)
            queued_group = handler.dispatch_post("/v1/enqueue", {
                "specification": group, "priority": 100,
            })
            handler.dispatch_post("/v1/enqueue", {
                "specification": {"program": "dp", "arguments": {"p": 5, "r": 3}},
            })
            self.assertIsNone(handler.dispatch_post(
                "/v1/lease", {"node_name": "node-3"})["job"])
            for index in range(2):
                with leader.connect(database) as connection:
                    connection.execute("UPDATE runs SET state='complete' WHERE run_id=?",
                                       (occupied[index],))
                self.assertIsNone(handler.dispatch_post(
                    "/v1/lease", {"node_name": f"node-{index}"})["job"])
            with leader.connect(database) as connection:
                connection.execute("UPDATE runs SET state='complete' WHERE run_id=?",
                                   (occupied[2],))
            leased = handler.dispatch_post("/v1/lease", {"node_name": "node-2"})["job"]
            self.assertEqual(leased["run_id"], queued_group["run_id"])
            self.assertEqual(len(leased["reserved_workers"]), 3)


# Help-only invocation performs no test workloads or network requests.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test runtime-ordered campaigns.\nExample: python3 tests/test_scheduling.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
