#!/usr/bin/env python3
"""Check tile format 2: exact compact packets and bands, refusal of damaged data, and a real field."""

from __future__ import annotations

import io
import json
import lzma
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver import bands, distributed, distributed_solver, tile_codec
from dp_solver.tiles import band_region, tile
from test_integration import find_run, request_json, wait_until
from test_recovery import Cluster

KERNEL = ROOT.parent / "dp_solver" / "kh_dp_tile"


def roundtrip_values(rows: list[bytes], width: int) -> bytes:
    """Encode then decode rows of uint64 values through an in-memory xz stream."""

    encoded = bytearray()
    tile_codec.encode_values(rows, width, encoded.extend)
    decoded = bytearray()
    tile_codec.decode_values(io.BytesIO(bytes(encoded)), len(rows), width, decoded.extend)
    return bytes(decoded)


class CodecTests(unittest.TestCase):
    """The row-delta byte-plane transform is exact for any data, not only monotone DP values."""

    def test_arbitrary_values_roundtrip_exactly(self) -> None:
        generator = random.Random(7)
        for width, rows in ((1, 1), (3, 70), (65, 129), (8, 64)):
            data = [b"".join(generator.getrandbits(64).to_bytes(8, sys.byteorder) for _ in range(width))
                    for _ in range(rows)]
            self.assertEqual(roundtrip_values(data, width), b"".join(data), (width, rows))

    def test_monotone_values_roundtrip_exactly(self) -> None:
        width, rows = 50, 200
        data = [b"".join((u + v + (u * v) // 7).to_bytes(8, sys.byteorder) for v in range(width))
                for u in range(rows)]
        self.assertEqual(roundtrip_values(data, width), b"".join(data))

    def test_choices_roundtrip_exactly(self) -> None:
        generator = random.Random(3)
        width, rows = 17, 130
        data = [bytes(generator.getrandbits(8) for _ in range(width * 4)) for _ in range(rows)]
        encoded = bytearray()
        tile_codec.encode_choices(data, encoded.extend)
        decoded = bytearray()
        tile_codec.decode_choices(io.BytesIO(bytes(encoded)), rows, width, decoded.extend)
        self.assertEqual(bytes(decoded), b"".join(data))


class PacketAndBandTests(unittest.TestCase):
    """Format-2 packets and bands carry real kernel output exactly and refuse anything else."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.p, cls.r, cls.side = 3, 7, 32
        cls.rectangle = tile(cls.p, cls.r, cls.side, 0, 0)
        halo = cls.root / "halo.bin"
        halo.write_bytes(b"\0" * cls.rectangle.halo_bytes)
        cls.output = cls.root / "kernel"
        subprocess.run([str(KERNEL), str(cls.p), str(cls.r), str(cls.rectangle.first_u), str(cls.rectangle.last_u),
                        str(cls.rectangle.first_v), str(cls.rectangle.last_v), str(halo), str(cls.output), "1"],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def identity(self, **changes) -> dict:
        return {**distributed_solver.packet_identity(self.p, self.r, self.rectangle), **changes}

    def packet(self, name: str, **changes) -> Path:
        path = self.root / name
        tile_codec.write_packet(self.output, self.side, self.side, self.identity(**changes), path)
        return path

    def test_packet_roundtrip_is_exact_and_deterministic(self) -> None:
        first, second = self.packet("one.xz"), self.packet("two.xz")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertTrue(tile_codec.is_xz(first))
        directory = self.root / "unpacked"
        distributed_solver.unpack(first, directory, self.rectangle, self.p, self.r)
        for name in ("values.bin", "choices.bin", "tile.json"):
            self.assertEqual((directory / name).read_bytes(), (self.output / name).read_bytes(), name)
        self.assertLess(first.stat().st_size, self.rectangle.value_bytes * 3 // 2 // 20)

    def test_packet_for_another_tile_or_field_is_refused(self) -> None:
        packet = self.packet("identity.xz")
        with self.assertRaises(ValueError):
            distributed_solver.unpack(packet, self.root / "other-tile", tile(self.p, self.r, self.side, 0, 1),
                                      self.p, self.r)
        with self.assertRaises(ValueError):
            distributed_solver.unpack(packet, self.root / "other-field", self.rectangle, self.p, self.r + 2)

    def test_truncated_garbled_and_long_packets_are_refused(self) -> None:
        good = self.packet("good.xz").read_bytes()
        plain = lzma.decompress(good)
        cases = {"truncated": good[:len(good) // 2],
                 "garbled": good[:12] + bytes(byte ^ 0x5A for byte in good[12:]),
                 "short": lzma.compress(plain[:-5], format=lzma.FORMAT_XZ),
                 "long": lzma.compress(plain + b"\0", format=lzma.FORMAT_XZ)}
        for name, payload in cases.items():
            path = self.root / f"bad-{name}.xz"
            path.write_bytes(payload)
            with self.assertRaises(ValueError, msg=name):
                distributed_solver.unpack(path, self.root / f"bad-{name}", self.rectangle, self.p, self.r)

    def test_band_roundtrip_matches_format_one(self) -> None:
        for kind in ("bottom", "right", "corner"):
            old, new = self.root / f"{kind}.gz", self.root / f"{kind}.xz"
            bands.write_band(self.output / "values.bin", self.p, self.r, self.rectangle, kind, old)
            bands.write_band(self.output / "values.bin", self.p, self.r, self.rectangle, kind, new, tile_format=2)
            first = bands.read_band(old, self.root / f"read-{kind}-1", self.p, self.r, self.rectangle, kind)
            second = bands.read_band(new, self.root / f"read-{kind}-2", self.p, self.r, self.rectangle, kind)
            self.assertEqual(first.region, band_region(self.p, self.rectangle, kind))
            self.assertEqual(first.region, second.region)
            self.assertEqual(first.path.read_bytes(), second.path.read_bytes(), kind)

    def test_wrong_or_damaged_band_raises_a_fallback_error(self) -> None:
        blob = self.root / "band.xz"
        bands.write_band(self.output / "values.bin", self.p, self.r, self.rectangle, "bottom", blob, tile_format=2)
        with self.assertRaises(ValueError):
            bands.read_band(blob, self.root / "band-kind", self.p, self.r, self.rectangle, "right")
        damaged = self.root / "band-damaged.xz"
        damaged.write_bytes(blob.read_bytes()[:-20])
        with self.assertRaises(ValueError):
            bands.read_band(damaged, self.root / "band-damaged", self.p, self.r, self.rectangle, "bottom")


class SpecificationTests(unittest.TestCase):
    """Only roots that choose format 2 change their tiles' identities."""

    def test_tile_format_reaches_children_only_when_chosen(self) -> None:
        def parent(arguments):
            return {"run_id": "root", "specification": json.dumps({"program": "dp_distributed",
                                                                   "arguments": arguments})}
        base = {"p": 3, "r": 7, "tile_side": 32}
        self.assertNotIn("tile_format", distributed.child_specification(parent(base), 0, 0)["arguments"])
        child = distributed.child_specification(parent({**base, "tile_format": 2}), 0, 0)
        self.assertEqual(child["arguments"]["tile_format"], 2)


class FormatTwoClusterTests(unittest.TestCase):
    """Real leader, agents and kernel: a format-2 field matches the dense reference split."""

    def test_format_two_split_matches_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cluster = Cluster(root)
            try:
                for name in ("a", "b", "c"):
                    cluster.worker(name, 1)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 3, "three workers")
                specification = {"program": "dp_distributed", "arguments": {
                    "p": 3, "r": 7, "tile_side": 32, "threads": 1, "artifact_format": "KHD1", "tile_format": 2}}
                queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": specification})

                def complete():
                    run = find_run(cluster.url, queued["run_id"])
                    if run["state"] == "failed":
                        raise AssertionError(run["error"])
                    return run if run["state"] == "complete" else None

                finished = wait_until(complete, "format-2 distributed DP", timeout=120)
                sys.path.insert(0, str(ROOT.parent / "dp_solver"))
                from artifacts import decode_dp
                from urllib.request import urlopen
                with urlopen(finished["artifact_location"]) as response:
                    artifact = decode_dp(response.read())
                reference = root / "reference.json"
                subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), "3", "7", "--raw-transitions",
                                "--work-dir", str(root / "raw"), "-o", str(reference)], check=True, capture_output=True)
                self.assertEqual(artifact, json.loads(reference.read_text()))
                with sqlite3.connect(cluster.database) as connection:
                    details = [json.loads(text) for (text,) in connection.execute(
                        "SELECT r.progress_details FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                        "WHERE t.parent_run_id=?", (queued["run_id"],))]
                    self.assertEqual(len(details), 9)
                    self.assertIn("bands", [item["input_mode"] for item in details])
                    for item in details:
                        for phase in ("fetch", "halo", "kernel", "pack", "publish"):
                            self.assertGreaterEqual(item[phase + "_seconds"], 0, item)
                    locations = [location for (location,) in connection.execute(
                        "SELECT p.location FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                        "JOIN replicas p ON p.artifact_hash=r.artifact_hash WHERE t.parent_run_id=?",
                        (queued["run_id"],))]
                    self.assertGreaterEqual(len(locations), 9)
                    for location in locations:
                        with urlopen(location) as response:
                            self.assertEqual(response.read(6), tile_codec.XZ_MAGIC)
            finally:
                cluster.close()


if __name__ == "__main__":
    unittest.main()
