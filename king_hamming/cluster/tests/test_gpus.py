#!/usr/bin/env python3
"""Check GPU registration, fencing, host locks, and match_gpu admission."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import gpus  # noqa: E402
import leader  # noqa: E402
from resources import ResourceRequest  # noqa: E402

EXAMPLE = ROOT.parent / "matching_solver" / "examples" / "13_5.khdp"
P600 = {"index": 0, "name": "Quadro P600", "arch": 61, "total_bytes": 2 * 1024**3}


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


if __name__ == "__main__":
    unittest.main()
