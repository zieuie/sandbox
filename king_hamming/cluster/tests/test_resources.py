#!/usr/bin/env python3
"""Check shared CPU/memory admission and future slot capacity arithmetic."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resources import (HOST_RESERVE_BYTES, NodeCapacity, ResourceRequest,
                       cpu_count, normalized_slots)


class ResourceTests(unittest.TestCase):
    def test_adapter_contract_is_strict(self) -> None:
        request = ResourceRequest.from_adapter({
            "coordinator_memory_bytes": 3,
            "worker_memory_bytes": 2,
            "min_cpu_count": 1,
        })
        self.assertEqual(request.memory_for("worker"), 2)
        for invalid in ({}, {"coordinator_memory_bytes": 0,
                             "worker_memory_bytes": 0, "min_cpu_count": True},
                        {"coordinator_memory_bytes": -1,
                         "worker_memory_bytes": 0, "min_cpu_count": 1}):
            with self.assertRaises(ValueError):
                ResourceRequest.from_adapter(invalid)

    def test_cpu_sets_are_counted_without_duplicates(self) -> None:
        self.assertEqual(cpu_count("0, 1,1,7"), 3)
        self.assertEqual(cpu_count(""), 0)

    def test_strict_jobs_reject_unknown_capacity_at_dispatch(self) -> None:
        request = ResourceRequest.from_adapter({
            "coordinator_memory_bytes": 1024**3, "worker_memory_bytes": 0,
            "min_cpu_count": 1, "require_known_capacity": True,
        })
        self.assertFalse(NodeCapacity(8, 0).fits(request, "coordinator"))
        self.assertFalse(NodeCapacity(0, 8 * 1024**3).fits(request, "coordinator"))
        self.assertTrue(NodeCapacity(8, 8 * 1024**3).fits(request, "coordinator"))
        with self.assertRaises(ValueError):
            ResourceRequest.from_adapter({"coordinator_memory_bytes": 1,
                                          "worker_memory_bytes": 0, "min_cpu_count": 1,
                                          "require_known_capacity": "yes"})

    def test_fit_retains_unknown_capacity_compatibility(self) -> None:
        request = ResourceRequest(4 * 1024**3, 3 * 1024**3, 2)
        self.assertTrue(NodeCapacity(0, 0).fits(request, "coordinator"))
        self.assertFalse(NodeCapacity(1, 16 * 1024**3).fits(request, "coordinator"))
        self.assertFalse(NodeCapacity(8, 5 * 1024**3).fits(request, "coordinator"))
        self.assertTrue(NodeCapacity(8, 7 * 1024**3).fits(request, "coordinator"))

    def test_safe_slots_respect_both_cpu_and_memory(self) -> None:
        gib = 1024**3
        capacity = NodeCapacity(12, 26 * gib)
        request = ResourceRequest(6 * gib, 4 * gib, 2)
        self.assertEqual(capacity.safe_slots(request, "coordinator"), 4)
        self.assertEqual(capacity.safe_slots(request, "worker"), 6)
        self.assertEqual(capacity.usable_memory_bytes, 26 * gib - HOST_RESERVE_BYTES)

    def test_registered_slots_are_disjoint_and_bounded_by_host_cpus(self) -> None:
        slots = normalized_slots([
            {"slot_id": 7, "cpu_set": "2,3"}, {"slot_id": 2, "cpu_set": "0,1"},
        ], "0,1,2,3")
        self.assertEqual([slot["slot_id"] for slot in slots], [2, 7])
        with self.assertRaises(ValueError):
            normalized_slots([
                {"slot_id": 0, "cpu_set": "0,1"},
                {"slot_id": 1, "cpu_set": "1,2"},
            ], "0,1,2")


if __name__ == "__main__":
    unittest.main()
