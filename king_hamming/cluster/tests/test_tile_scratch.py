#!/usr/bin/env python3
"""Tile scratch in RAM: claims are bounded, dead claims are swept, and the disk is the fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver import tile_scratch

MiB = 1024**2


class TileScratchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"KH_TILE_SCRATCH": str(self.root),
                                                   "KH_TILE_SCRATCH_MAX_BYTES": str(100 * MiB)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_claims_are_bounded_together_and_released(self) -> None:
        first = tile_scratch.claim(60 * MiB)
        self.assertIsNotNone(first)
        self.assertTrue(first.is_dir())
        self.assertIsNone(tile_scratch.claim(60 * MiB), "both together exceed the limit: use the disk")
        second = tile_scratch.claim(40 * MiB)
        self.assertIsNotNone(second)
        tile_scratch.release(first)
        self.assertFalse(first.exists())
        self.assertIsNotNone(tile_scratch.claim(60 * MiB), "a released claim frees its room")

    def test_a_dead_process_claim_is_swept(self) -> None:
        child = subprocess.run([sys.executable, "-c", (
            "import sys; sys.path.insert(0, sys.argv[1]); from dp_solver import tile_scratch; "
            "print(tile_scratch.claim(90 * 1024**2))"), str(ROOT.parent)],
            capture_output=True, text=True, check=True, env=dict(os.environ))
        leaked = Path(child.stdout.strip())
        self.assertTrue(leaked.is_dir(), "the child exited without releasing its claim")
        self.assertIsNotNone(tile_scratch.claim(90 * MiB), "the dead child's claim no longer counts")
        self.assertFalse(leaked.exists())

    def test_a_reused_pid_does_not_keep_a_claim_alive(self) -> None:
        stale = self.root / f"{tile_scratch.PREFIX}{os.getpid()}-old"
        stale.mkdir()
        (stale / tile_scratch.CLAIM_NAME).write_text(json.dumps({"pid": os.getpid(), "start": "0", "bytes": 90 * MiB}))
        self.assertIsNotNone(tile_scratch.claim(90 * MiB))
        self.assertFalse(stale.exists())

    def test_disk_mode_and_a_missing_root_fall_back_to_the_disk(self) -> None:
        with mock.patch.dict(os.environ, {"KH_TILE_SCRATCH": "disk"}):
            self.assertIsNone(tile_scratch.claim(MiB))
        with mock.patch.dict(os.environ, {"KH_TILE_SCRATCH": str(self.root / "missing")}):
            self.assertIsNone(tile_scratch.claim(MiB))

    def test_a_full_tmpfs_falls_back_to_the_disk(self) -> None:
        with mock.patch.dict(os.environ, {"KH_TILE_SCRATCH_MAX_BYTES": str(1 << 60)}):
            stats = os.statvfs(self.root)
            self.assertIsNone(tile_scratch.claim(stats.f_bavail * stats.f_frsize))


if __name__ == "__main__":
    unittest.main()
