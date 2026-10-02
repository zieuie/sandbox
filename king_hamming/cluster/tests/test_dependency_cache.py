#!/usr/bin/env python3
"""Exercise immutable, bounded DP dependency reuse without a real network."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blob_store import blob_path
from dependency_cache import checkout, evict


class DependencyCacheTests(unittest.TestCase):
    def test_local_blob_is_verified_and_pinned_without_download(self) -> None:
        payload = b"tile packet" * 100
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "storage"
            source = blob_path(storage, digest)
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            with patch("dependency_cache.fetch_blob") as download:
                packet = checkout(root / "shared", root / "private", digest,
                                  len(payload), ["http://peer/blobs/" + digest], storage)
            download.assert_not_called()
            source.unlink()  # Storage GC cannot invalidate this lease's hard link.
            self.assertEqual(packet.read_bytes(), payload)

    def test_two_leases_fetch_identical_packet_only_once(self) -> None:
        payload = b"different tile packet" * 100
        digest = hashlib.sha256(payload).hexdigest()
        calls = []

        def download(partial, _location, _size, _check):
            calls.append(1)
            time.sleep(0.03)
            partial.write_bytes(payload)
            return 0

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = "http://peer/blobs/" + digest
            with patch("blob_store.download_suffix", side_effect=download):
                with ThreadPoolExecutor(max_workers=2) as workers:
                    packets = list(workers.map(
                        lambda index: checkout(root / "shared", root / str(index),
                                               digest, len(payload), [url]), range(2)))
            self.assertEqual(len(calls), 1)
            self.assertEqual([packet.read_bytes() for packet in packets], [payload, payload])
            self.assertEqual(evict(root / "shared", 0, 0), len(payload))
            self.assertEqual([packet.read_bytes() for packet in packets], [payload, payload])

    def test_corrupt_local_blob_falls_back_and_prefers_own_url(self) -> None:
        payload = b"trusted packet" * 100
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "storage"
            source = blob_path(storage, digest)
            source.parent.mkdir(parents=True)
            source.write_bytes(b"x" * len(payload))
            shared = blob_path(root / "shared", digest)
            shared.parent.mkdir(parents=True)
            shared.write_bytes(payload)
            own = "http://own/blobs/" + digest
            peer = "http://peer/blobs/" + digest
            with patch("dependency_cache.fetch_blob", return_value=shared) as fetch:
                packet = checkout(root / "shared", root / "private", digest,
                                  len(payload), [peer, own], storage, "http://own")
            self.assertEqual(fetch.call_args.args[3], [own, peer])
            self.assertEqual(packet.read_bytes(), payload)

    def test_inactive_partial_is_bounded_after_published_objects(self) -> None:
        payload = b"partial input"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downloads = root / ".downloads"
            downloads.mkdir()
            partial = downloads / (digest + ".part")
            partial.write_bytes(payload)
            self.assertEqual(evict(root, 0, 0), len(payload))
            self.assertFalse(partial.exists())


if __name__ == "__main__":
    unittest.main()
