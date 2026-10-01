#!/usr/bin/env python3
"""Local multi-owner checkpoint, pause, resume and invalid-image tests."""
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from matching_solver.artifacts import load_dp, verify
from matching_solver_multi.submit import specification
from matching_solver_multi import state


class RuntimeTests(unittest.TestCase):
    def test_pause_restore_and_corrupt_image_rejection(self):
        source = ROOT / "examples/7_5.khdp"
        dp, digest = load_dp(source)
        spec = specification(source, "4,1,0,0,0,1", workers=2, threads=1, batch=4096)
        spec["arguments"]["checkpoint_phases"] = 1
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus) < 2:
            self.skipTest("needs two isolated logical CPUs")
        with tempfile.TemporaryDirectory(prefix="kh-owned-runtime-") as folder:
            root = Path(folder)
            (root / "spec.json").write_text(json.dumps(spec))
            (root / "peers.json").write_text(json.dumps([dict(host="127.0.0.1", cpus=str(cpu)) for cpu in cpus[:2]]))
            command = [sys.executable, str(ROOT / "matching_solver_multi/coordinator.py"),
                       "--specification", str(root / "spec.json"), "--participants", str(root / "peers.json"),
                       "--output", str(root / "result.bin"), "--checkpoint-seconds", "1800"]
            with (root / "log").open("wb") as log:
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True, start_new_session=True)
                try:
                    for raw in child.stdout:
                        event = json.loads(raw)
                        if event.get("event") == "checkpoint":
                            child.send_signal(signal.SIGTERM)
                            break
                    self.assertEqual(child.wait(timeout=30), 75, (root / "log").read_text())
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                    child.stdout.close()
            paths = {state.name(i): root / "owner-state" / state.name(i) for i in range(2)}
            phase, done = state.validate(paths, dp, digest, spec["arguments"]["poly"], 2)
            self.assertEqual((phase, done), state.validate_python(paths, dp, digest, spec["arguments"]["poly"], 2))
            self.assertGreater(phase, 0)
            self.assertGreater(done, 0)
            raw = paths[state.name(0)].read_bytes()
            broken = bytearray(raw)
            broken[0] ^= 1
            paths[state.name(0)].write_bytes(broken)
            with self.assertRaises(ValueError):
                state.validate(paths, dp, digest, spec["arguments"]["poly"], 2)
            paths[state.name(0)].write_bytes(raw)
            # A structurally valid but mathematically wrong choice must be
            # rejected by native restore before it resumes the matching.
            wrong_edge = bytearray(raw)
            offset = state.HEADER.size + 4 * len(spec["arguments"]["poly"])
            for at in range(offset, len(raw), 16):
                uid, mate, choice, reserved = struct.unpack_from("<4I", raw, at)
                if mate != state.NONE:
                    struct.pack_into("<I", wrong_edge, at + 8, (choice + 1) % dp["f"])
                    break
            paths[state.name(0)].write_bytes(wrong_edge)
            rejected = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("image assignment is not an edge", rejected.stderr)
            self.assertFalse((root / "result.bin").exists())
            paths[state.name(0)].write_bytes(raw)
            # Simulate interruption partway through certificate packing.
            (root / "payload.bin").write_bytes(b"incomplete export")
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(verify(root / "result.bin", dp, digest)["verified"])
            messages = [json.loads(raw) for raw in result.stdout.splitlines()]
            cursors = [row["cursor"] for row in messages if row.get("event") == "checkpoint"]
            self.assertTrue(cursors and min(cursors) > phase)
            self.assertEqual(messages[-1]["done"], dp["q"])


if __name__ == "__main__":
    unittest.main()
