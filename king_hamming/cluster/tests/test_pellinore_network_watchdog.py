#!/usr/bin/env python3
"""Test Pellinore's conservative NetworkManager recovery policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "pellinore_network_watchdog.py"
SPEC = importlib.util.spec_from_file_location("pellinore_network_watchdog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def test_healthy_probe_clears_failure_streak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            restarted = []
            for _ in range(2):
                watchdog.check(state, lambda: False, lambda: restarted.append(1),
                               lambda: 1000, lambda _: None)
            result = watchdog.check(state, lambda: True, lambda: restarted.append(1),
                                    lambda: 1000, lambda _: None)
            self.assertEqual(result, "healthy")
            self.assertEqual(watchdog.read_number(state / "failures"), 0)
            self.assertEqual(restarted, [])

    def test_sustained_failure_restarts_once_with_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            restarted = []
            now = [1000]
            call = lambda: watchdog.check(
                state, lambda: False, lambda: restarted.append(1),
                lambda: now[0], lambda _: None,
            )
            self.assertEqual(call(), "failure 1/3")
            self.assertEqual(call(), "failure 2/3")
            self.assertEqual(call(), "restarted NetworkManager")
            self.assertEqual(restarted, [1])
            self.assertEqual([call(), call(), call()], [
                "failure 1/3", "failure 2/3", "cooldown",
            ])
            now[0] += 600
            self.assertEqual(call(), "restarted NetworkManager")
            self.assertEqual(restarted, [1, 1])

    def test_confirmation_avoids_transient_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            restarted = []
            probes = iter((False, False, False, True))
            for _ in range(2):
                watchdog.check(state, lambda: next(probes), lambda: restarted.append(1),
                               lambda: 1000, lambda _: None)
            result = watchdog.check(state, lambda: next(probes),
                                    lambda: restarted.append(1),
                                    lambda: 1000, lambda _: None)
            self.assertEqual(result, "recovered before restart")
            self.assertEqual(watchdog.read_number(state / "failures"), 0)
            self.assertEqual(restarted, [])


if __name__ == "__main__":
    unittest.main()
