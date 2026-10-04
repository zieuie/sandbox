#!/usr/bin/env python3
"""The opt-in CPU sampler charges stacks that use CPU and ignores threads that only wait."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sampler


def burn_cpu(stop: threading.Event) -> None:
    while not stop.is_set():
        sum(i * i for i in range(2000))


def wait_quietly(stop: threading.Event) -> None:
    stop.wait()


class SamplerTests(unittest.TestCase):
    def test_busy_thread_is_charged_and_idle_thread_is_not(self) -> None:
        stop = threading.Event()
        threads = [threading.Thread(target=burn_cpu, args=(stop,)), threading.Thread(target=wait_quietly, args=(stop,))]
        for thread in threads:
            thread.start()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profile.txt"
            watcher = sampler.CpuSampler(output, interval=0.02, write_seconds=0.2)
            watcher.start()
            time.sleep(1.5)
            stop.set()
            watcher.stop_event.set()
            for thread in threads:
                thread.join()
            watcher.join(timeout=2)
            watcher.write()
            text = output.read_text()
        self.assertIn("burn_cpu", text)
        self.assertNotIn("wait_quietly", text)

    def test_off_unless_requested(self) -> None:
        os.environ.pop("KH_LEADER_PROFILE", None)
        self.assertIsNone(sampler.start_if_requested(Path("/nonexistent/profile.txt")))


if __name__ == "__main__":
    unittest.main()
