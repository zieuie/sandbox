#!/usr/bin/env python3
"""Check durable tile index resume, replica fallback, and exact distributed split reconstruction."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dp_solver import tile_driver

ROOT = Path(__file__).resolve().parents[1]


# Validate the real standalone driver through retained stop/resume and shard loss.
class DriverTests(unittest.TestCase):
    """Exercise resumable wave execution without any household SSH."""

    def test_resume_after_primary_shard_loss_matches_raw(self) -> None:
        """The second verified copy permits exact resume after losing a committed primary."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            output = root / "result.json"
            workers = min(2, len(os.sched_getaffinity(0)))
            command = [sys.executable, str(ROOT / "tile_driver.py"), "5", "3", "--work-dir", str(work),
                       "--tile-side", "7", "--workers", str(workers), "-o", str(output)]
            stopped = subprocess.run([*command, "--stop-after-waves", "2"], capture_output=True)
            self.assertEqual(stopped.returncode, 75, stopped.stderr.decode())
            self.assertFalse(output.exists())
            with sqlite3.connect(work / "tiles.sqlite") as connection:
                previous = dict(((row, column), result) for row, column, result in connection.execute("SELECT row,column,result FROM tiles"))
                record = json.loads(previous[(0, 0)])
            if workers > 1:
                self.assertEqual(len(record["copies"]), 2)
                shutil.rmtree(record["location"])
            completed = subprocess.run(command, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            with sqlite3.connect(work / "tiles.sqlite") as connection:
                current = dict(((row, column), result) for row, column, result in connection.execute("SELECT row,column,result FROM tiles"))
                self.assertEqual(len(current), 16)
                self.assertTrue(all(current[key] == value for key, value in previous.items()))
            reference = root / "reference.json"
            subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), "5", "3", "--raw-transitions",
                            "--work-dir", str(root / "raw"), "-o", str(reference)], check=True, capture_output=True)
            self.assertEqual(json.loads(output.read_text()), json.loads(reference.read_text()))
            subprocess.run([str(ROOT.parent / "dp_solver" / "verify_dp.py"), str(output)], check=True, capture_output=True)
            rejected = subprocess.run(command, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)

    def test_compute_failure_retries_another_worker(self) -> None:
        """An uncommitted failure moves to a different worker and records its incident."""

        if len(os.sched_getaffinity(0)) < 2:
            self.skipTest("two local CPUs required")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = tile_driver.execute
            failed = []

            def execute(host, worker_root, arguments, target, halo, cpus):
                """Fail only the first primary attempt; run all replacements through the C kernel."""

                if not failed:
                    failed.append(worker_root)
                    raise OSError("injected worker loss")
                self.assertNotEqual(worker_root, failed[0])
                return original(host, worker_root, arguments, target, halo, cpus)

            arguments = [str(ROOT / "tile_driver.py"), "3", "3", "--work-dir", str(root),
                         "--tile-side", "9", "--workers", "2", "-o", str(root / "result.json")]
            with patch.object(sys, "argv", arguments), patch.object(tile_driver, "execute", side_effect=execute):
                self.assertEqual(tile_driver.main(), 0)
            with sqlite3.connect(root / "tiles.sqlite") as connection:
                record = json.loads(connection.execute("SELECT result FROM tiles").fetchone()[0])
            self.assertIn("worker-1", record["location"])
            self.assertIn("injected worker loss", record["attempt_failures"][0])
            self.assertEqual(len(record["copies"]), 2)

    def test_resume_rejects_changed_identity(self) -> None:
        """Changing tile geometry never overwrites a retained calculation's index."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = [sys.executable, str(ROOT / "tile_driver.py"), "3", "3", "--work-dir", str(root),
                       "--tile-side", "4", "-o", str(root / "result.json"), "--stop-after-waves", "1"]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 75)
            changed = command.copy()
            changed[changed.index("--tile-side") + 1] = "5"
            rejected = subprocess.run(changed, capture_output=True)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn(b"resume parameters", rejected.stderr)


# No arguments print help instead of running experiments.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test resumable immutable DP tiles.\nExample: python3 tests/test_tile_driver.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
