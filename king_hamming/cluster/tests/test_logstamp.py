#!/usr/bin/env python3
"""Every line the leader, agents and feeder log carries its local time."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import logstamp
from test_integration import free_port


class LogstampTests(unittest.TestCase):
    def test_each_line_is_stamped_once_including_partial_writes(self) -> None:
        sink = io.StringIO()
        stream = logstamp.Stamped(sink)
        print("one", file=stream)
        stream.write("tw")
        stream.write("o\nthree\n\nfo")
        stream.write("ur\n")
        lines = sink.getvalue().splitlines()
        self.assertEqual([logstamp.split(line)[1] for line in lines], ["one", "two", "three", "", "four"])
        self.assertTrue(all(logstamp.split(line)[0] is not None for line in lines))

    def test_split_reads_the_time_and_leaves_unstamped_lines_alone(self) -> None:
        when, text = logstamp.split("2026-10-04T20:31:05.123-05:00 leader listening on x")
        self.assertEqual(text, "leader listening on x")
        self.assertAlmostEqual(when, 1791163865.123, places=3)
        self.assertEqual(logstamp.split("Traceback (most recent call last):"), (None, "Traceback (most recent call last):"))
        self.assertEqual(logstamp.split("2026-10-04 not a stamp")[0], None)

    def test_threads_never_share_a_stamp(self) -> None:
        sink = io.StringIO()
        stream = logstamp.Stamped(sink)

        def write(name: str) -> None:
            for index in range(200):
                print(f"{name} {index}", file=stream)

        threads = [threading.Thread(target=write, args=(name,)) for name in "abcd"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        lines = sink.getvalue().splitlines()
        self.assertEqual(len(lines), 800)
        self.assertTrue(all(logstamp.split(line)[0] is not None for line in lines))

    def test_a_served_leader_stamps_its_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "leader.log"
            with log_path.open("ab") as log:
                leader = subprocess.Popen(
                    [sys.executable, str(ROOT / "leader.py"), "serve", "--database", str(Path(directory) / "l.sqlite"),
                     "--listen", f"127.0.0.1:{free_port()}"], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while "leader listening" not in log_path.read_text() and time.monotonic() < deadline:
                    time.sleep(0.1)
            finally:
                leader.terminate()
                leader.wait(timeout=10)
            line = next(line for line in log_path.read_text().splitlines() if "leader listening" in line)
            when, text = logstamp.split(line)
            self.assertIsNotNone(when, line)
            self.assertTrue(text.startswith("leader listening on"))
            self.assertLess(abs(time.time() - when), 60)


if __name__ == "__main__":
    unittest.main()
