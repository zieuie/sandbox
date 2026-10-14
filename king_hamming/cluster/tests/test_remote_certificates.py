#!/usr/bin/env python3
"""Certificates archived on another machine (campaigns/remote_certificates.py), with ssh run locally."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from campaigns import remote_certificates  # noqa: E402


def local_ssh(host: str, command: str, timeout: float = 0) -> bytes:
    return subprocess.run(["bash", "-c", command], check=True, stdout=subprocess.PIPE).stdout


class RemoteCertificateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.data = b"KHM1" + bytes(range(256)) * 40
        self.digest = hashlib.sha256(self.data).hexdigest()
        (self.directory / "archived").write_bytes(self.data)
        self.path = self.directory / "7_13_run.khmatch"
        self.record = {"host": "x", "path": str(self.directory / "archived"),
                       "bytes": len(self.data), "sha256": self.digest}
        self.original = remote_certificates.ssh
        remote_certificates.ssh = local_ssh
        self.addCleanup(setattr, remote_certificates, "ssh", self.original)

    def write_marker(self, **changes) -> None:
        (self.directory / "7_13_run.khmatch.remote").write_text(json.dumps({**self.record, **changes}))

    def test_no_marker_or_local_file_means_none(self) -> None:
        self.assertIsNone(remote_certificates.marker(self.path))
        self.write_marker()
        self.assertEqual(remote_certificates.marker(self.path)["bytes"], len(self.data))
        self.path.write_bytes(b"local")
        self.assertIsNone(remote_certificates.marker(self.path), "a file that is here wins")

    def test_verified_header_returns_the_first_bytes(self) -> None:
        header, size = remote_certificates.verified_header(self.record, self.digest)
        self.assertEqual((header.read(4), size), (b"KHM1", len(self.data)))

    def test_a_changed_file_or_hash_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            remote_certificates.verified_header(self.record, "0" * 64)
        (self.directory / "archived").write_bytes(self.data + b"!")
        with self.assertRaises(ValueError):
            remote_certificates.verified_header(self.record, self.digest)


if __name__ == "__main__":
    unittest.main()
