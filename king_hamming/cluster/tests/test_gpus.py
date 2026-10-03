#!/usr/bin/env python3
"""Check GPU registration, fencing, host locks, and match_gpu / match_gpu_blocks admission."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import gpus  # noqa: E402
import leader  # noqa: E402
from resources import ResourceRequest  # noqa: E402

EXAMPLE = ROOT.parent / "matching_solver" / "examples" / "13_5.khdp"
P600 = {"index": 0, "name": "Quadro P600", "arch": 61, "total_bytes": 2 * 1024**3}


# 13^5 needs about 29 MiB on a device: a 20 MiB lease holds only blocks of it, and a few of them.
MIB = 1024**2
SMALL = {"index": 0, "name": "Small", "arch": 61, "total_bytes": 256 * MIB + 20 * MIB}


def block_job(device_bytes: int) -> dict:
    from gpu_block_match_solver.submit import specification
    return specification(EXAMPLE, "2,4,0,0,0,1", threads=2, gpu_memory_bytes=device_bytes)


def gpu_job() -> dict:
    from gpu_match_solver.submit import specification
    return specification(EXAMPLE, "2,4,0,0,0,1", threads=2)


class GPURecordTests(unittest.TestCase):
    def test_normalized_rejects_malformed_devices(self) -> None:
        self.assertEqual(gpus.normalized(None), [])
        self.assertEqual(gpus.normalized([P600])[0]["name"], "Quadro P600")
        for bad in ([{"index": -1, "total_bytes": 1}], [{"index": 0, "total_bytes": 0}],
                    [P600, P600], "x", [{"index": True, "total_bytes": 5}]):
            with self.assertRaises(ValueError):
                gpus.normalized(bad)

    def test_choose_skips_busy_and_small_devices(self) -> None:
        big = {"index": 1, "name": "big", "arch": 86, "total_bytes": 6 * 1024**3}
        self.assertEqual(gpus.choose([P600, big], set(), 1024**3), 0)
        self.assertEqual(gpus.choose([P600, big], {0}, 1024**3), 1)
        self.assertEqual(gpus.choose([P600, big], set(), 3 * 1024**3), 1)
        self.assertIsNone(gpus.choose([P600], set(), 2 * 1024**3))

    def test_resource_request_validates_gpu_memory(self) -> None:
        base = {"coordinator_memory_bytes": 1, "worker_memory_bytes": 0, "min_cpu_count": 1}
        self.assertEqual(ResourceRequest.from_adapter(base).gpu_memory_bytes, 0)
        self.assertEqual(ResourceRequest.from_adapter({**base, "gpu_memory_bytes": 5}).gpu_memory_bytes, 5)
        for bad in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                ResourceRequest.from_adapter({**base, "gpu_memory_bytes": bad})

    def test_device_lock_is_exclusive_and_honours_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["KH_GPU_LOCK_DIR"] = directory
            gpus.LOCK_DIRECTORY = Path(directory)
            first, second = gpus.DeviceLock(0), gpus.DeviceLock(0)
            self.assertTrue(first.acquire(0))
            self.assertFalse(second.acquire(0.1))
            self.assertFalse(second.acquire(30, should_stop=lambda: True))
            first.release()
            self.assertTrue(second.acquire(0))
            second.release()
            # A long hold (block matching) makes an opportunistic waiter give up at once; a normal one does not.
            self.assertTrue(first.acquire(0, hold="long"))
            started = time.monotonic()
            self.assertFalse(second.acquire(30, skip_long=True))
            self.assertLess(time.monotonic() - started, 2)
            first.release()
            self.assertTrue(first.acquire(0))
            self.assertFalse(second.acquire(0.2, skip_long=True))  # short hold: waits its timeout
            first.release()
            self.assertTrue(second.acquire(0, skip_long=True))  # marker cleared on release
            second.release()
            gpus.mark_unavailable(3)
            self.assertTrue(gpus.recently_unavailable(3))
            self.assertFalse(gpus.recently_unavailable(4))

    def test_detect_tolerates_missing_probe(self) -> None:
        self.assertEqual(gpus.detect(Path("/nonexistent/kh_cuda_probe")), [])


class GPUSchedulingTests(unittest.TestCase):
    def handler(self, directory: str):
        database = Path(directory) / "leader.sqlite"
        leader.initialize(database, 1800)
        return database, object.__new__(leader.make_handler(database))

    def register(self, handler, name: str, devices: list[dict]) -> None:
        handler.dispatch_post("/v1/register", {
            "node_name": name, "cpu_set": "0,1,2,3", "memory_bytes": 16 * 1024**3,
            "slots": [{"slot_id": index, "cpu_set": str(index)} for index in range(4)],
            "gpus": devices,
        })

    def test_gpu_jobs_need_a_device_and_are_fenced_per_device(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "cpu-only", [])
            self.register(handler, "gpu", [P600])
            for rerun in (False, True):
                handler.dispatch_post("/v1/enqueue", {
                    "specification": gpu_job(), "priority": 100, "rerun": rerun})
            self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "cpu-only", "slot_id": 0})["job"])
            job = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 0})["job"]
            self.assertEqual(job["specification"]["program"], "match_gpu")
            self.assertEqual(job["gpu_index"], 0)
            self.assertEqual(len(job["assigned_cpu_set"].split(",")), 2)
            # The second GPU job waits for the device even though CPUs remain free.
            self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 1})["job"])
            with leader.connect(database) as connection:
                stored = connection.execute("SELECT gpu_index FROM runs WHERE run_id=?",
                                            (job["run_id"],)).fetchone()[0]
            self.assertEqual(stored, 0)
            handler.dispatch_post("/v1/requeue", {"run_id": job["run_id"], "lease_token": job["lease_token"]})
            again = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 1})["job"]
            self.assertEqual(again["gpu_index"], 0)

    def test_gpu_job_shares_host_with_dp_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, handler = self.handler(directory)
            self.register(handler, "gpu", [P600])
            handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": 3, "r": 3, "tile_side": 4, "threads": 1, "max_cpus": 1,
                    "max_visits": 10**9, "max_tile_bytes": 2 * 1024**3}}})
            tile = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 0})["job"]
            self.assertEqual(tile["specification"]["program"], "dp_tile")
            self.assertIsNone(tile["gpu_index"])
            handler.dispatch_post("/v1/enqueue", {"specification": gpu_job(), "priority": 100})
            job = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 1})["job"]
            self.assertEqual(job["specification"]["program"], "match_gpu")
            self.assertTrue(set(job["assigned_cpu_set"].split(",")).isdisjoint(tile["assigned_cpu_set"].split(",")))

    def test_heartbeat_keeps_registered_devices_and_status_reports_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "gpu", [P600])
            with leader.connect(database) as connection:
                session = connection.execute("SELECT session_id FROM nodes").fetchone()[0]
            handler.dispatch_post("/v1/heartbeat", {"node_name": "gpu", "session_id": session,
                                                    "cpu_set": "0,1,2,3"})
            with leader.connect(database) as connection:
                node = connection.execute("SELECT * FROM nodes").fetchone()
            self.assertEqual(gpus.from_record(node)[0]["total_bytes"], P600["total_bytes"])

    def test_oversized_field_never_leases_on_small_gpu(self) -> None:
        from gpu_match_solver.adapter import GPUMatchingAdapter
        with tempfile.TemporaryDirectory() as directory:
            _, handler = self.handler(directory)
            tiny = {"index": 0, "name": "tiny", "arch": 61, "total_bytes": 256 * 1024**2 + 1024}
            self.register(handler, "gpu", [tiny])
            job = gpu_job()
            self.assertGreater(GPUMatchingAdapter().resource_requirements(job)["gpu_memory_bytes"], 1024)
            handler.dispatch_post("/v1/enqueue", {"specification": job})
            self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 0})["job"])


class GPUPolicyTests(unittest.TestCase):
    def test_feeder_plan_prefers_a_fitting_gpu(self) -> None:
        sys.path.insert(0, str(ROOT.parent))
        from campaigns import gpu_policy
        from matching_solver.artifacts import load_dp
        dp, _ = load_dp(EXAMPLE)
        node = {"node_name": "gpu", "state": "healthy", "compute_enabled": 1,
                "memory_bytes": 16 * 1024**3, "gpus_json": '[{"index":0,"name":"P600","arch":61,"total_bytes":2147483648}]'}
        plan = gpu_policy.plan(dp, {}, [node])
        self.assertEqual(plan["program"], "match_gpu")
        self.assertEqual(plan["hosts"], ["gpu"])
        self.assertIsNone(gpu_policy.plan(dp, {"gpu_matching": False}, [node]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [{**node, "gpus_json": "[]"}]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [{**node, "state": "unavailable"}]))


class GPUUsageSampleTests(unittest.TestCase):
    handler = GPUSchedulingTests.handler
    register = GPUSchedulingTests.register

    def test_normalized_stats_drops_malformed_entries(self) -> None:
        good = {"index": 0, "util_percent": 40, "memory_used_bytes": 5}
        self.assertEqual(gpus.normalized_stats([good, {"index": 1}, {**good, "util_percent": 101},
                                                {**good, "index": -1}, "x", {**good, "memory_used_bytes": "n"}]), [good])
        self.assertEqual(gpus.normalized_stats("x"), [])
        self.assertEqual(len(gpus.normalized_stats([{**good, "index": i} for i in range(40)])), 16)

    def test_heartbeat_stores_samples_and_ignores_agents_without_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "gpu", [P600])
            with leader.connect(database) as connection:
                session = connection.execute("SELECT session_id FROM nodes").fetchone()[0]
            beat = {"node_name": "gpu", "session_id": session, "cpu_set": "0,1,2,3"}
            handler.dispatch_post("/v1/heartbeat", beat)  # an older agent: no samples, still accepted
            handler.dispatch_post("/v1/heartbeat", {**beat, "gpu_stats": [
                {"index": 0, "util_percent": 73, "memory_used_bytes": 123456}, {"index": 9, "util_percent": 500, "memory_used_bytes": 1}]})
            with leader.connect(database) as connection:
                rows = connection.execute("SELECT node_name,gpu_index,util_percent,memory_used_bytes FROM gpu_usage_samples").fetchall()
            self.assertEqual([tuple(row) for row in rows], [("gpu", 0, 73, 123456)])


class GPUBlockTests(unittest.TestCase):
    """match_gpu_blocks: planned only when no GPU holds the whole field, fenced to a device that fits."""

    handler = GPUSchedulingTests.handler
    register = GPUSchedulingTests.register

    def setUp(self) -> None:
        from gpu_block_match_solver import adapter
        patcher = unittest.mock.patch.object(adapter, "MIN_DEVICE_BYTES", MIB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def node(self, name: str, device: dict, ram: int = 16 * 1024**3) -> dict:
        import json
        return {"node_name": name, "state": "healthy", "compute_enabled": 1, "memory_bytes": ram,
                "gpus_json": json.dumps([device])}

    def test_plan_uses_blocks_only_when_the_whole_field_does_not_fit(self) -> None:
        from campaigns import gpu_policy
        from matching_solver.artifacts import load_dp
        dp, _ = load_dp(EXAMPLE)
        plan = gpu_policy.plan(dp, {}, [self.node("small", SMALL)])
        self.assertEqual((plan["program"], plan["reason"], plan["hosts"]), ("match_gpu_blocks", "admitted: GPU blocks", ["small"]))
        self.assertEqual(plan["gpu_memory_bytes"], 20 * MIB)
        self.assertGreaterEqual(plan["blocks"], 2)
        self.assertEqual(plan["workers"], 1)
        # A GPU that holds the field keeps the single-GPU program.
        self.assertEqual(gpu_policy.plan(dp, {}, [self.node("small", SMALL), self.node("p600", P600)])["program"], "match_gpu")
        self.assertIsNone(gpu_policy.plan(dp, {"gpu_block_matching": False}, [self.node("small", SMALL)]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [self.node("small", SMALL, ram=64 * MIB)]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [{**self.node("small", SMALL), "state": "unavailable"}]))

    def test_plan_offers_every_host_with_the_memory_and_sizes_the_lease_to_the_smallest(self) -> None:
        from campaigns import gpu_policy
        from matching_solver.artifacts import load_dp
        dp, _ = load_dp(EXAMPLE)
        bigger = {**SMALL, "total_bytes": 256 * MIB + 24 * MIB}
        plan = gpu_policy.plan(dp, {}, [self.node("a", SMALL), self.node("b", bigger), self.node("c", bigger)])
        self.assertEqual((plan["hosts"], plan["gpu_memory_bytes"]), (["a", "b", "c"], 20 * MIB))
        # A host without the RAM is left out, however good its GPU.
        plan = gpu_policy.plan(dp, {}, [self.node("a", SMALL, ram=64 * MIB), self.node("b", bigger)])
        self.assertEqual((plan["hosts"], plan["gpu_memory_bytes"]), (["b"], 24 * MIB))

    def test_adapter_validates_and_requests_the_planned_device(self) -> None:
        from gpu_block_match_solver.adapter import GPUBlockMatchingAdapter
        adapter = GPUBlockMatchingAdapter()
        job = block_job(20 * MIB)
        adapter.validate(job)
        self.assertEqual(adapter.resource_requirements(job)["gpu_memory_bytes"], 20 * MIB)
        self.assertEqual(job["program"], "match_gpu_blocks")
        for bad in (0, 100, 2**41, True):
            broken = block_job(20 * MIB)
            broken["arguments"]["gpu_memory_bytes"] = bad
            with self.assertRaises(ValueError):
                adapter.validate(broken)
        # Too little device memory for even a one-cell block is rejected when queued.
        with self.assertRaises(ValueError):
            adapter.validate(block_job(2 * MIB))

    def test_block_jobs_lease_only_on_a_device_that_fits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            tiny = {**SMALL, "total_bytes": 256 * MIB + 4 * MIB}
            self.register(handler, "tiny", [tiny])
            self.register(handler, "small", [SMALL])
            handler.dispatch_post("/v1/enqueue", {"specification": block_job(20 * MIB), "priority": 100})
            self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "tiny", "slot_id": 0})["job"])
            job = handler.dispatch_post("/v1/lease", {"node_name": "small", "slot_id": 0})["job"]
            self.assertEqual((job["specification"]["program"], job["gpu_index"]), ("match_gpu_blocks", 0))

    def test_command_runs_the_block_bridge(self) -> None:
        from gpu_block_match_solver.adapter import GPUBlockMatchingAdapter
        command = GPUBlockMatchingAdapter().command(block_job(20 * MIB), Path("/x/out.khmatch"), None, 0)
        self.assertTrue(command[1].endswith("gpu_block_match_solver/cluster_solver.py"))
        self.assertIn("--poly", command)


if __name__ == "__main__":
    unittest.main()
