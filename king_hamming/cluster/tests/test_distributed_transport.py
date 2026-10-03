#!/usr/bin/env python3
"""A congested leader must not turn a healthy DP reconstruction into an engine failure."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver import distributed_solver


class DistributedTransportTests(unittest.TestCase):
    def test_transient_leader_timeout_retries_same_request(self) -> None:
        arguments = SimpleNamespace(leader="http://leader")
        body = {"run_id": "root", "lease_token": "lease", "row": 0, "column": 0}
        with patch.object(distributed_solver, "STOP", False), patch.object(
                distributed_solver, "request_json", side_effect=[TimeoutError("busy"), {"records": []}]) as request:
            self.assertEqual(distributed_solver.leader_request(arguments, "/v1/tile-input", body),
                             {"records": []})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])

    def test_stale_lease_is_not_retried(self) -> None:
        arguments = SimpleNamespace(leader="http://leader")
        error = HTTPError("http://leader/v1/tile-input", 409, "stale", {}, None)
        with patch.object(distributed_solver, "STOP", False), patch.object(
                distributed_solver, "request_json", side_effect=error) as request:
            with self.assertRaises(InterruptedError):
                distributed_solver.leader_request(arguments, "/v1/tile-input", {})
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
