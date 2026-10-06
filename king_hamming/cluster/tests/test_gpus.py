#!/usr/bin/env python3
"""Check GPU registration, fencing, host locks, and match_gpu / match_gpu_blocks / match_gpu_wide admission."""

from __future__ import annotations

import json
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

    def test_no_dp_tiles_on_a_host_whose_gpu_a_matching_holds(self) -> None:
        """2026-10-05: a block matching held merlin's GPU for hours and merlin's slots ran 31^7
        tiles on CPUs instead (25-50 min each), saturating the leader's machine."""
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "gpu", [P600])
            handler.dispatch_post("/v1/enqueue", {"specification": gpu_job(), "priority": 100})
            job = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 0})["job"]
            self.assertEqual(job["specification"]["program"], "match_gpu")
            handler.dispatch_post("/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": 3, "r": 3, "tile_side": 4, "threads": 1, "max_cpus": 1,
                    "max_visits": 10**9, "max_tile_bytes": 2 * 1024**3}}})
            self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 1})["job"],
                              "its only GPU is leased, and tiles may not fall back to CPUs")
            with leader.connect(database) as connection:
                connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('dp_cpu_fallback','1')")
            tile = handler.dispatch_post("/v1/lease", {"node_name": "gpu", "slot_id": 1})["job"]
            self.assertEqual(tile["specification"]["program"], "dp_tile", "allowed again once CPUs are")

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
        from gpu_wide_match_solver import adapter as wide
        for module in (adapter, wide):
            patcher = unittest.mock.patch.object(module, "MIN_DEVICE_BYTES", MIB)
            patcher.start()
            self.addCleanup(patcher.stop)

    def node(self, name: str, device: dict, ram: int = 16 * 1024**3, disk: int = 2 * 1024**4) -> dict:
        import json
        return {"node_name": name, "state": "healthy", "compute_enabled": 1, "memory_bytes": ram,
                "gpus_json": json.dumps([device]), "storage_free_bytes": disk}

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
        # Without block matching the wide matcher takes it; with neither, nothing does.
        self.assertEqual(gpu_policy.plan(dp, {"gpu_block_matching": False}, [self.node("small", SMALL)])["program"],
                         "match_gpu_wide")
        self.assertIsNone(gpu_policy.plan(dp, {"gpu_block_matching": False, "gpu_wide_matching": False},
                                          [self.node("small", SMALL)]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [self.node("small", SMALL, ram=64 * MIB)]))
        self.assertIsNone(gpu_policy.plan(dp, {}, [{**self.node("small", SMALL), "state": "unavailable"}]))

    def test_blocker_names_the_limit_that_binds(self) -> None:
        from campaigns import gpu_policy
        from gpu_block_match_solver import adapter
        from matching_solver.artifacts import load_dp
        dp, _ = load_dp(EXAMPLE)
        self.assertIsNone(gpu_policy.blocker(dp, {}, [self.node("small", SMALL)]))   # block mode takes it
        self.assertIsNone(gpu_policy.blocker(dp, {"gpu_matching": False}, [self.node("small", SMALL)]))
        self.assertEqual(gpu_policy.blocker(dp, {}, [self.node("cpu", SMALL) | {"gpus_json": "[]"}]),
                         "no machine with a GPU is online")
        # Every binding limit is named: the block matcher's, then the wide matcher's.
        note = gpu_policy.blocker(dp, {}, [self.node("small", SMALL, ram=64 * MIB)])
        self.assertIn("block matching needs", note)
        self.assertIn("wide matching needs at least", note)
        off = {"gpu_block_matching": False, "gpu_wide_matching": False}
        self.assertEqual(gpu_policy.blocker(dp, off, [self.node("small", SMALL)]),
                         "block matching is turned off; wide matching is turned off")
        with unittest.mock.patch.object(adapter, "MAX_Q", dp["q"] - 1):
            self.assertEqual(gpu_policy.plan(dp, {}, [self.node("small", SMALL)])["program"], "match_gpu_wide")
            self.assertEqual(gpu_policy.blocker(dp, {"gpu_wide_matching": False}, [self.node("small", SMALL)]),
                             f"q = {dp['q']:,} is above the block matcher's {dp['q'] - 1:,}; "
                             "wide matching is turned off")

    def test_fields_above_2_32_go_to_block_mode_on_a_host_with_the_ram(self) -> None:
        from campaigns import gpu_policy
        from gpu_block_match_solver.adapter import GPUBlockMatchingAdapter, host_bytes
        from gpu_block_match_solver.submit import specification
        from gpu_match_solver.adapter import GPUMatchingAdapter
        from matching_solver.artifacts import load_dp
        big = ROOT.parent / "examples" / "13_9.khdp"   # the campaign's 13^9 DP result: q = 2.47 * 2^32
        dp, _ = load_dp(big)
        self.assertGreater(dp["q"], 2**32)
        rtx = {"index": 0, "name": "RTX 3060", "arch": 86, "total_bytes": 6 * 1024**3}
        plan = gpu_policy.plan(dp, {}, [self.node("merlin", rtx, ram=40 * 1024**3), self.node("small", rtx)])
        self.assertEqual((plan["program"], plan["hosts"]), ("match_gpu_blocks", ["merlin"]))
        # Rows of the used cells only (a_max = 5 of 13) and choices in a scratch file: under 20 GiB.
        self.assertLess(host_bytes(dp, 4), 20 * 1024**3)
        self.assertGreater(host_bytes(dp, 4, choice_file=False), 35 * 1024**3)
        # A host without the block matcher's RAM takes it in row passes instead.
        self.assertEqual(gpu_policy.plan(dp, {}, [self.node("small", rtx)])["program"], "match_gpu_wide")
        self.assertIn("of host RAM", gpu_policy.blocker(dp, {"gpu_wide_matching": False}, [self.node("small", rtx)]))
        job = specification(big, "2,7,0,0,0,0,0,0,0,1", threads=4, gpu_memory_bytes=plan["gpu_memory_bytes"])
        GPUBlockMatchingAdapter().validate(job)
        with self.assertRaisesRegex(ValueError, "uint32 labels"):
            GPUMatchingAdapter().validate({**job, "program": "match_gpu"})

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



class GPUWideTests(unittest.TestCase):
    """match_gpu_wide: planned past the block matcher's limits (F, q, rows in RAM), sized to the host."""

    handler = GPUSchedulingTests.handler
    register = GPUSchedulingTests.register
    node = GPUBlockTests.node

    def setUp(self) -> None:
        from gpu_wide_match_solver import adapter
        patcher = unittest.mock.patch.object(adapter, "MIN_DEVICE_BYTES", MIB)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def wide_dp(p: int, r: int) -> dict:
        """A full-field DP document (every cell of every coset requested, so n = q), as for 5^15."""
        f = p ** (r // 2)
        return {"p": p, "r": r, "q": p ** r, "f": f, "runs": [{"a": p, "t": f, "repeat": 1}]}

    def test_fields_past_the_block_matcher_go_wide_on_merlin_only(self) -> None:
        from campaigns import gpu_policy
        from gpu_wide_match_solver import adapter
        rtx = {"index": 0, "name": "RTX 3060", "arch": 86, "total_bytes": 6 * 1024**3}
        t1000 = {"index": 0, "name": "T1000", "arch": 75, "total_bytes": 4 * 1024**3}
        nodes = [self.node("merlin", rtx, ram=40 * 1024**3), self.node("gawain", t1000, ram=8 * 1024**3),
                 self.node("p600", P600, ram=8 * 1024**3)]
        for p, r in ((7, 13), (5, 15)):
            dp = self.wide_dp(p, r)
            plan = gpu_policy.plan(dp, {}, nodes)
            self.assertEqual((plan["program"], plan["hosts"]), ("match_gpu_wide", ["merlin"]), (p, r))
            # Three quarters of merlin's memory after the reserve: rows in passes, not all at once.
            self.assertEqual(plan["max_bytes"], gpu_policy.wide_usable(nodes[0]))
            self.assertLess(plan["max_bytes"], adapter.all_rows_bytes(dp, 4))
            self.assertGreater(plan["blocks"], 100)
            self.assertIsNone(gpu_policy.blocker(dp, {}, nodes))
        # 7^13 needs a 12 GB endpoint bitmap to verify, which gawain can't hold.
        note = gpu_policy.blocker(self.wide_dp(7, 13), {}, nodes[1:])
        self.assertIn("F = 117,649 is above the block matcher's 65,534", note)
        self.assertIn("q = 96,889,010,407 is above the block matcher's", note)
        self.assertIn("wide matching needs at least", note)
        # The certificate must fit on the host's disk with the free-space floor to spare: today
        # merlin has about 146 GB free, enough for 5^15 (65 GB) but not for 7^13 (206 GB).
        today = [self.node("merlin", rtx, ram=40 * 1024**3, disk=146 * 10**9)]
        self.assertEqual(gpu_policy.plan(self.wide_dp(5, 15), {}, today)["program"], "match_gpu_wide")
        self.assertIsNone(gpu_policy.plan(self.wide_dp(7, 13), {}, today))
        self.assertIn("certificate; the GPU machine with the memory for it has",
                      gpu_policy.blocker(self.wide_dp(7, 13), {}, today))
        self.assertGreater(gpu_policy.certificate_bytes(self.wide_dp(7, 13)), 200 * 10**9)
        # Another machine's free disk doesn't count: gawain has the disk but not the memory.
        self.assertIn("with the memory for it has", gpu_policy.blocker(
            self.wide_dp(7, 13), {}, today + [self.node("gawain", t1000, ram=8 * 1024**3, disk=859 * 10**9)]))
        # Smaller fields fit everywhere, but a wide run still goes only to the largest GPU (merlin),
        # sized for it: on 2026-10-06 5^15 was first planned for any P600 with 743 small blocks.
        p600s = [self.node(f"p600-{i}", P600, ram=16 * 1024**3) for i in range(4)]
        plan = gpu_policy.plan(self.wide_dp(5, 15), {}, today + p600s)
        self.assertEqual((plan["hosts"], plan["gpu_memory_bytes"]), (["merlin"], gpus.usable_bytes(rtx)))
        # p = 2 can't mark 'unmatched' in the payload (F is a power of two).
        self.assertIsNone(gpu_policy.wide_plan(self.wide_dp(2, 37), {}, nodes, 4))

    def test_adapter_validates_and_runs_the_wide_bridge(self) -> None:
        from gpu_wide_match_solver.adapter import GPUWideMatchingAdapter, minimum_host_bytes
        from gpu_wide_match_solver.submit import specification
        adapter = GPUWideMatchingAdapter()
        job = specification(EXAMPLE, "2,4,0,0,0,1", threads=2, gpu_memory_bytes=20 * MIB)
        adapter.validate(job)
        self.assertEqual(job["program"], "match_gpu_wide")
        self.assertEqual(adapter.resource_requirements(job)["gpu_memory_bytes"], 20 * MIB)
        self.assertEqual(adapter.resource_requirements(job)["coordinator_memory_bytes"], job["arguments"]["max_bytes"])
        command = adapter.command(job, Path("/x/out.khmatch"), None, 0)
        self.assertTrue(command[1].endswith("gpu_wide_match_solver/cluster_solver.py"))
        from matching_solver.artifacts import load_dp
        dp, _ = load_dp(EXAMPLE)
        small = specification(EXAMPLE, "2,4,0,0,0,1", threads=2, gpu_memory_bytes=20 * MIB,
                              max_bytes=minimum_host_bytes(dp, 2, 20 * MIB) - 1)
        with self.assertRaisesRegex(ValueError, "below the wide matcher's minimum"):
            adapter.validate(small)
        with self.assertRaises(ValueError):
            adapter.validate(specification(EXAMPLE, "2,4,0,0,0,1", threads=2, gpu_memory_bytes=2 * MIB))
        files = [target for _, target in adapter.runtime_files()]
        self.assertIn("gpu_wide_match_solver/kh_verify_wide", files)

    def test_wide_jobs_lease_on_a_device_that_fits(self) -> None:
        from gpu_wide_match_solver.submit import specification
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "small", [SMALL])
            job = specification(EXAMPLE, "2,4,0,0,0,1", threads=2, gpu_memory_bytes=20 * MIB)
            handler.dispatch_post("/v1/enqueue", {"specification": job, "priority": 100})
            leased = handler.dispatch_post("/v1/lease", {"node_name": "small", "slot_id": 0})["job"]
            self.assertEqual((leased["specification"]["program"], leased["gpu_index"]), ("match_gpu_wide", 0))


class DPTileGPUAdmissionTests(unittest.TestCase):
    """A DP tile too big for the card goes to the CPU without marking the GPU unavailable."""

    def test_large_tiles_skip_the_gpu(self) -> None:
        from dp_solver import distributed_solver
        from dp_solver.tiles import tile
        p600 = {"index": 0, "name": "Quadro P600", "arch": 61, "total_bytes": 2088632320}
        rtx = {"index": 1, "name": "RTX 3060 Laptop", "arch": 86, "total_bytes": 6086262784}
        cube = tile(127, 3, 512, 30, 30)  # about 2.2 GiB
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KH_GPU_DP_MAX_BYTES", None)
            os.environ.pop("KH_GPU_DEVICES", None)
            self.assertTrue(distributed_solver.gpu_fits(13, tile(13, 9, 4096, 12, 9)))
            self.assertTrue(distributed_solver.gpu_fits(113, tile(113, 3, 512, 20, 20)))
            self.assertFalse(distributed_solver.gpu_fits(127, cube))
            self.assertFalse(distributed_solver.gpu_fits(3, tile(3, 25, 16384, 5, 5)))
            os.environ["KH_GPU_DEVICES"] = json.dumps([p600, rtx])
            self.assertFalse(distributed_solver.gpu_fits(127, cube, 0))
            self.assertTrue(distributed_solver.gpu_fits(127, cube, 1))
            os.environ["KH_GPU_DEVICES"] = "not json"
            self.assertFalse(distributed_solver.gpu_fits(127, cube, 1))
            os.environ["KH_GPU_DP_MAX_BYTES"] = str(8 * 1024**3)
            self.assertTrue(distributed_solver.gpu_fits(127, cube, 0))


class DPTileGPUWaitTests(unittest.TestCase):
    """How long a tile waits for the GPU follows what its CPUs would cost."""

    def wait(self, p: int, r: int, side: int, row: int, column: int, threads: int = 2) -> float:
        from dp_solver import distributed_solver
        from dp_solver.tiles import tile
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KH_GPU_DP_WAIT_SECONDS", None)
            return distributed_solver.gpu_wait_seconds(p, tile(p, r, side, row, column), threads)

    def test_heavy_tiles_wait_long_for_the_gpu_and_light_ones_barely_at_all(self) -> None:
        heavy = self.wait(29, 7, 4096, 30, 30)          # about 1,380 s on two CPUs
        self.assertGreater(heavy, 600)
        self.assertLessEqual(heavy, 900)
        self.assertLess(self.wait(5, 13, 1024, 16, 16), 2.0)       # well under a second on the CPU
        medium = self.wait(7, 13, 4096, 30, 30)          # about 16 s
        self.assertTrue(4 < medium < 12, medium)

    def test_more_threads_shorten_the_wait_and_an_explicit_setting_wins(self) -> None:
        from dp_solver import distributed_solver
        from dp_solver.tiles import tile
        rectangle = tile(23, 7, 4096, 20, 20)
        two = self.wait(23, 7, 4096, 20, 20, 2)
        eight = self.wait(23, 7, 4096, 20, 20, 8)
        self.assertAlmostEqual(two / eight, 4, delta=0.5)
        with unittest.mock.patch.dict(os.environ, {"KH_GPU_DP_WAIT_SECONDS": "7"}):
            self.assertEqual(distributed_solver.gpu_wait_seconds(23, rectangle, 2), 7.0)


if __name__ == "__main__":
    unittest.main()
