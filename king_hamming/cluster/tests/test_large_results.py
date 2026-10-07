#!/usr/bin/env python3
"""Large results are linked, not copied: in-place KHM1 publication, the blob store, the feeder's archive."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import artifact_resolver  # noqa: E402
import blob_store  # noqa: E402
from matching_solver import artifacts  # noqa: E402

EXAMPLE = ROOT.parent / "matching_solver" / "examples" / "13_5.khdp"


class BlobLinkTests(unittest.TestCase):
    def test_large_results_are_linked_small_ones_and_default_callers_copied(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                unittest.mock.patch.object(blob_store, "LINK_BYTES", 1000):
            root = Path(directory) / "blobs"
            big, small = Path(directory) / "big.khmatch", Path(directory) / "small.khmatch"
            big.write_bytes(os.urandom(5000))
            small.write_bytes(os.urandom(500))
            digest, path = blob_store.store_blob(big, root, link=True)
            self.assertEqual(digest, hashlib.sha256(big.read_bytes()).hexdigest())
            self.assertEqual(path.stat().st_ino, big.stat().st_ino)        # one file, two names
            big.unlink()                                                   # the run directory goes
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            _, small_path = blob_store.store_blob(small, root, link=True)
            self.assertNotEqual(small_path.stat().st_ino, small.stat().st_ino)
            again = Path(directory) / "again.bin"
            again.write_bytes(os.urandom(5000))
            _, copied = blob_store.store_blob(again, root)                 # checkpoints etc.: copied
            self.assertNotEqual(copied.stat().st_ino, again.stat().st_ino)
            self.assertEqual(sorted(item.name for item in root.iterdir() if item.name.startswith(".")), [])


class ArchiveLinkTests(unittest.TestCase):
    def test_a_local_blob_is_hash_checked_and_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "blobs"
            source = Path(directory) / "result"
            source.write_bytes(os.urandom(4000))
            digest, path = blob_store.store_blob(source, root)
            run = {"artifact_hash": digest, "artifact_location": "http://127.0.0.1:9/blobs/" + digest}
            output = Path(directory) / "matching-results" / "5_15.khmatch"
            artifact_resolver.retrieve(run, output, 10_000, local_roots=[root])
            self.assertEqual(output.stat().st_ino, path.stat().st_ino)
            # A damaged local copy is not linked; with no other live source, retrieval fails.
            other = Path(directory) / "other.khmatch"
            path.chmod(0o600)
            data = bytearray(path.read_bytes())
            data[0] ^= 1
            damaged = Path(directory) / "damaged"
            damaged.write_bytes(bytes(data))
            os.replace(damaged, path)
            with self.assertRaisesRegex(RuntimeError, "local blob differs"):
                artifact_resolver.retrieve(run, other, 10_000, local_roots=[root], timeout=1)
            self.assertFalse(other.exists())


SHM = Path("/dev/shm")


@unittest.skipUnless(SHM.is_dir() and SHM.stat().st_dev != Path(tempfile.gettempdir()).stat().st_dev,
                     "needs a second filesystem (/dev/shm)")
class SecondDriveTests(unittest.TestCase):
    """merlin's /mnt/khdata: 7^13's 206 GB certificate fits on neither disk twice (2026-10-07)."""

    def test_a_result_on_the_large_drive_is_parked_symlinked_archived_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory(dir=SHM) as drive, \
                unittest.mock.patch.object(blob_store, "LINK_BYTES", 1000), \
                unittest.mock.patch.object(artifact_resolver, "LINK_BYTES", 1000):
            root, large = Path(directory) / "blobs", Path(drive)
            work = large / "work" / "run" / "token"
            work.mkdir(parents=True)
            result = work / "result.bin"
            data = os.urandom(5000)
            result.write_bytes(data)
            # Without the large root it would be copied onto the store's disk; with it, parked.
            digest, path = blob_store.store_blob(result, root, link=True, large_root=large)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            self.assertTrue(path.is_symlink())
            self.assertEqual(path.resolve(), (large / "blobs" / digest).resolve())
            self.assertFalse(result.exists())                               # moved, not copied
            self.assertEqual(path.read_bytes(), data)
            # The feeder's archive on the other disk: checked, then symlinked.
            run = {"artifact_hash": digest, "artifact_location": "http://127.0.0.1:9/blobs/" + digest}
            output = Path(directory) / "matching-results" / "7_13.khmatch"
            artifact_resolver.retrieve(run, output, 10_000, local_roots=[root])
            self.assertTrue(output.is_symlink())
            self.assertEqual(output.read_bytes(), data)
            # Storing the same result again (a rerun) keeps one parked copy.
            again = work / "again.bin"
            again.write_bytes(data)
            self.assertEqual(blob_store.store_blob(again, root, link=True, large_root=large), (digest, path))
            self.assertEqual(len(list((large / "blobs").iterdir())), 1)
            # Garbage collection removes the link and the parked bytes.
            self.assertEqual(blob_store.remove_blob(path), len(data))
            self.assertFalse(path.exists() or path.is_symlink())
            self.assertFalse((large / "blobs" / digest).exists())


class InPlacePublishTests(unittest.TestCase):
    def test_in_place_publication_matches_a_copied_one(self) -> None:
        dp, digest = artifacts.load_dp(EXAMPLE)
        n = artifacts.request_count(dp)
        metadata = {"p": dp["p"], "r": dp["r"], "required": n, "status": 0, "matched": n,
                    "polynomial": [2, 4, 0, 0, 0, 1]}
        header = artifacts.header(dp, digest, metadata)
        payload = os.urandom(3000)
        with tempfile.TemporaryDirectory() as directory:
            raw, copied = Path(directory) / "raw.bin", Path(directory) / "copied.khmatch"
            raw.write_bytes(payload)
            artifacts.publish(copied, header, raw)
            work, published = Path(directory) / "certificate.partial", Path(directory) / "result.khmatch"
            work.write_bytes(bytes(len(header)) + payload)
            seen = []
            artifacts.publish_in_place(work, published, header, progress=lambda done, size: seen.append(done))
            self.assertEqual(published.read_bytes(), copied.read_bytes())
            self.assertFalse(work.exists())
            self.assertEqual(seen[-1], len(header) + len(payload))
            # The header space must be untouched zeros, and the final name must be new.
            work.write_bytes(b"x" + bytes(len(header) - 1) + payload)
            with self.assertRaisesRegex(ValueError, "reserved header space"):
                artifacts.publish_in_place(work, Path(directory) / "other.khmatch", header)
            work.write_bytes(bytes(len(header)) + payload)
            with self.assertRaises(FileExistsError):
                artifacts.publish_in_place(work, published, header)


class VerifierChoiceTests(unittest.TestCase):
    def test_wide_verifier_memory_is_rows_free(self) -> None:
        # 7^13: the endpoint bitmap and offsets, not 166 GB of rows, set the floor.
        dp = {"p": 7, "r": 13, "q": 7**13, "f": 7**6, "runs": [{"a": 3, "t": 1, "repeat": 1}]}
        fixed = artifacts.wide_fixed_memory(dp, dp["q"], dp["f"], 12)
        self.assertLess(fixed, 13 * 1024**3)
        self.assertGreater(fixed, (dp["q"] + 7) // 8)
        self.assertGreater(artifacts.native_memory(dp, dp["q"], dp["f"]), 160 * 1024**3)


if __name__ == "__main__":
    unittest.main()
