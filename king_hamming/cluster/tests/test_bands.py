#!/usr/bin/env python3
"""Check that edge bands carry exactly what a successor tile's halo needs, and travel safely."""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import leader
from dp_solver import bands, distributed
from dp_solver.tiles import band_kind, band_region, build_halo, dependencies, needed_bands, piece_t, tile, whole
from test_integration import find_run, request_json, wait_until
from test_recovery import Cluster

KERNEL = ROOT.parent / "dp_solver" / "kh_dp_tile"


def run_tiles(root: Path, p: int, r: int, side: int):
    """Compute every tile in wave order from whole predecessor tiles; return halos, outputs and the grid size."""

    from dp_solver.scheduling import dp_estimate
    budget = dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})["budget"]
    count = (budget + side - 1) // side
    sources, halos, outputs = {}, {}, {}
    for wave in range(2 * count - 1):
        for row in range(count):
            column = wave - row
            if not 0 <= column < count:
                continue
            target = tile(p, r, side, row, column)
            halo = root / f"halo-{row}-{column}.bin"
            build_halo(p, r, side, target, sources, halo)
            halos[(row, column)] = halo.read_bytes()
            output = root / f"tile-{row}-{column}"
            subprocess.run([str(KERNEL), str(p), str(r), str(target.first_u), str(target.last_u),
                            str(target.first_v), str(target.last_v), str(halo), str(output), "1"],
                           check=True, capture_output=True)
            outputs[(row, column)] = output / "values.bin"
            sources[(row, column)] = output / "values.bin"
    return halos, outputs, count


def halos_from_bands(root: Path, p: int, r: int, side: int, outputs, count):
    """Rebuild every halo from unpacked bands of the finished tiles; return them keyed by tile."""

    rebuilt = {}
    cut = {}
    for (row, column), values in outputs.items():
        rectangle = tile(p, r, side, row, column)
        for kind in needed_bands(p, r, side, rectangle):
            blob = root / f"b-{row}-{column}-{kind}.khband"
            bands.write_band(values, p, r, rectangle, kind, blob)
            cut[(row, column, kind)] = blob
    for row in range(count):
        for column in range(count):
            target = tile(p, r, side, row, column)
            sources = {}
            for predecessor in dependencies(p, r, side, target):
                kind = band_kind(target, predecessor)
                piece = bands.read_band(cut[(predecessor.row, predecessor.column, kind)],
                                        root / f"u-{row}-{column}-{predecessor.row}-{predecessor.column}",
                                        p, r, predecessor, kind)
                sources[(predecessor.row, predecessor.column)] = piece
            halo = root / f"rebuilt-{row}-{column}.bin"
            build_halo(p, r, side, target, sources, halo)
            rebuilt[(row, column)] = halo.read_bytes()
    return rebuilt


class BandGeometryTests(unittest.TestCase):
    """Bands must reproduce every halo byte, whether the band is thinner than the tile or the whole tile."""

    def check(self, p: int, r: int, side: int) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            halos, outputs, count = run_tiles(root, p, r, side)
            rebuilt = halos_from_bands(root, p, r, side, outputs, count)
            self.assertGreater(count, 2)
            self.assertEqual(halos.keys(), rebuilt.keys())
            for key in halos:
                self.assertEqual(halos[key], rebuilt[key], f"halo of tile {key}")

    def test_band_thinner_than_tile(self) -> None:
        """With p^2 below the tile side only the edge rows and columns travel."""

        p, r, side = 3, 7, 32
        self.assertLess(p * p, side)
        self.check(p, r, side)
        target = tile(p, r, side, 1, 1)
        up, left = tile(p, r, side, 0, 1), tile(p, r, side, 1, 0)
        self.assertLess(band_region(p, up, "bottom").value_bytes, whole(up).value_bytes)
        self.assertLess(band_region(p, left, "right").value_bytes, whole(left).value_bytes)
        self.assertEqual(band_region(p, tile(p, r, side, 0, 0), "corner").value_bytes, p**4 * 8)
        self.assertEqual([band_kind(target, item) for item in dependencies(p, r, side, target)],
                         ["corner", "bottom", "right"])

    def test_band_as_wide_as_tile(self) -> None:
        """When p^2 exceeds the tile side the band is the whole tile and halos are still exact."""

        self.check(5, 3, 7)
        self.check(3, 5, 4)

    def test_edge_tiles_publish_only_bands_something_can_read(self) -> None:
        p, r, side = 3, 7, 32
        self.assertEqual(needed_bands(p, r, side, tile(p, r, side, 0, 0)), ("bottom", "right", "corner"))
        self.assertEqual(needed_bands(p, r, side, tile(p, r, side, 2, 0)), ("right",))
        self.assertEqual(needed_bands(p, r, side, tile(p, r, side, 0, 2)), ("bottom",))
        self.assertEqual(needed_bands(p, r, side, tile(p, r, side, 2, 2)), ())

    def test_piece_that_misses_a_halo_cell_is_refused(self) -> None:
        p, r, side = 3, 7, 32
        target = tile(p, r, side, 1, 0)
        above = tile(p, r, side, 0, 0)
        thin = piece_t(band_region(p, above, "corner"), Path("unused"))
        with tempfile.TemporaryDirectory() as temporary:
            blob = Path(temporary) / "values.bin"
            blob.write_bytes(b"\0" * thin.region.value_bytes)
            with self.assertRaises(ValueError):
                build_halo(p, r, side, target, {(0, 0): piece_t(thin.region, blob)}, Path(temporary) / "halo")


class BandBlobTests(unittest.TestCase):
    """A band blob is deterministic and refuses anything that is not exactly the band asked for."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.p, self.r, self.side = 3, 7, 32
        self.rectangle = tile(self.p, self.r, self.side, 1, 1)
        self.values = self.root / "values.bin"
        self.values.write_bytes(bytes(i % 251 for i in range(self.rectangle.value_bytes)))

    def write(self, kind="bottom", name="band") -> Path:
        blob = self.root / name
        bands.write_band(self.values, self.p, self.r, self.rectangle, kind, blob)
        return blob

    def read(self, blob, kind="bottom", rectangle=None, name="out"):
        return bands.read_band(blob, self.root / name, self.p, self.r, rectangle or self.rectangle, kind)

    def test_roundtrip_is_exact_and_deterministic(self) -> None:
        first, second = self.write(name="one"), self.write(name="two")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        piece = self.read(first)
        region = band_region(self.p, self.rectangle, "bottom")
        self.assertEqual(piece.region, region)
        raw = self.values.read_bytes()
        width = self.rectangle.last_v - self.rectangle.first_v + 1
        first_row = region.first_u - self.rectangle.first_u
        self.assertEqual(piece.path.read_bytes(), raw[first_row * width * 8:])

    def test_wrong_kind_tile_or_field_is_refused(self) -> None:
        blob = self.write("bottom")
        with self.assertRaises(ValueError):
            self.read(blob, "right", name="a")
        with self.assertRaises(ValueError):
            self.read(blob, rectangle=tile(self.p, self.r, self.side, 1, 0), name="b")
        with self.assertRaises(ValueError):
            bands.read_band(blob, self.root / "c", self.p, self.r + 2, self.rectangle, "bottom")

    def test_short_long_and_garbled_blobs_are_refused(self) -> None:
        good = gzip.decompress(self.write().read_bytes())
        line, body = good.split(b"\n", 1)
        for name, payload in (("short", line + b"\n" + body[:-8]), ("long", line + b"\n" + body + b"\0"),
                              ("nojson", b"not json\n" + body),
                              ("noline", b"x" * 5000)):
            blob = self.root / name
            blob.write_bytes(gzip.compress(payload))
            with self.assertRaises(ValueError, msg=name):
                self.read(blob, name="r-" + name)
        garbled = self.root / "garbled"
        garbled.write_bytes(b"not gzip at all")
        with self.assertRaises(OSError):
            self.read(garbled, name="r-garbled")

    def test_blob_is_much_smaller_than_the_tile(self) -> None:
        zeros = self.root / "zeros.bin"
        zeros.write_bytes(b"\0" * self.rectangle.value_bytes)
        blob = self.root / "zero-band"
        bands.write_band(zeros, self.p, self.r, self.rectangle, "right", blob)
        self.assertLess(blob.stat().st_size, 1024)


class LeaderBandTests(unittest.TestCase):
    """The leader indexes published bands by packet hash and offers them only while they have a live source."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "leader.sqlite"
        leader.initialize(self.database, 1800)
        self.handler = object.__new__(leader.make_handler(self.database))
        for name in ("a", "b"):
            self.handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})
        self.p, self.r, self.side = 5, 3, 7
        self.packet = "a" * 64

    def enqueue(self) -> str:
        return self.handler.dispatch_post("/v1/enqueue", {"specification": {
            "program": "dp_distributed", "arguments": {"p": self.p, "r": self.r, "tile_side": self.side, "threads": 1}}})["run_id"]

    def make_running_tile(self, parent: str, row: int, column: int, node: str = "a") -> sqlite3.Row:
        """Force one child into the running state under a known lease, ignoring scheduling."""

        with leader.connect(self.database) as connection:
            child = connection.execute(
                "SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                "WHERE t.parent_run_id=? AND t.row=? AND t.column=?", (parent, row, column)).fetchone()
            connection.execute("UPDATE runs SET state='running',node_name=?,lease_token=? WHERE run_id=?",
                               (node, "t" * 36, child["run_id"]))
            return connection.execute("SELECT * FROM runs WHERE run_id=?", (child["run_id"],)).fetchone()

    def complete(self, parent: str, row: int, column: int, digest: str, copies=("a", "b")) -> None:
        now = time.time()
        with leader.connect(self.database) as connection:
            child = connection.execute(
                "SELECT child_run_id FROM distributed_tiles WHERE parent_run_id=? AND row=? AND column=?",
                (parent, row, column)).fetchone()[0]
            connection.execute("INSERT OR IGNORE INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,99)",
                               (digest, now))
            connection.execute("UPDATE runs SET state='complete',artifact_hash=?,finished=? WHERE run_id=?",
                               (digest, now, child))
            for name in copies:
                connection.execute("INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                                   (digest, name, f"http://{name}/packet", now))
            distributed.advance(connection, now)

    def publish(self, run, kinds=("bottom", "right", "corner"), packet=None) -> dict:
        bands_ = {kind: {"sha256": chr(ord("c") + index) * 64, "size": 1234, "location": f"http://a:8042/blobs/{kind}"}
                  for index, kind in enumerate(kinds)}
        with leader.connect(self.database) as connection:
            return distributed.inputs(connection, run, {"publish_bands": {"packet": packet or self.packet, "bands": bands_}},
                                      time.time())

    def test_published_bands_become_descriptor_entries_for_the_right_successor(self) -> None:
        parent = self.enqueue()
        origin = self.make_running_tile(parent, 0, 0)
        self.assertEqual(self.publish(origin), {"registered": 3})
        self.complete(parent, 0, 0, self.packet)
        below = self.make_running_tile(parent, 1, 0)
        with leader.connect(self.database) as connection:
            page = distributed.inputs(connection, below, {}, time.time())
        record = page["records"][0]
        self.assertEqual((record["row"], record["column"]), (0, 0))
        self.assertEqual(record["sha256"], self.packet)
        self.assertEqual(record["band"]["kind"], "bottom")
        self.assertEqual(record["band"]["size"], 1234)
        self.assertEqual(record["band"]["locations"], ["http://a:8042/blobs/bottom"])
        right = self.make_running_tile(parent, 0, 1)
        with leader.connect(self.database) as connection:
            self.assertEqual(distributed.inputs(connection, right, {}, time.time())["records"][0]["band"]["kind"], "right")

    def test_tile_without_bands_still_gets_the_whole_packet_descriptor(self) -> None:
        parent = self.enqueue()
        self.make_running_tile(parent, 0, 0)
        self.complete(parent, 0, 0, self.packet)
        below = self.make_running_tile(parent, 1, 0)
        with leader.connect(self.database) as connection:
            record = distributed.inputs(connection, below, {}, time.time())["records"][0]
        self.assertNotIn("band", record)
        self.assertEqual(record["locations"], ["http://a/packet", "http://b/packet"])

    def test_band_without_a_live_source_is_not_offered(self) -> None:
        parent = self.enqueue()
        origin = self.make_running_tile(parent, 0, 0)
        self.publish(origin)
        self.complete(parent, 0, 0, self.packet)
        with leader.connect(self.database) as connection:
            connection.execute("DELETE FROM replicas WHERE artifact_hash IN (SELECT band_hash FROM tile_bands)")
        below = self.make_running_tile(parent, 1, 0)
        with leader.connect(self.database) as connection:
            self.assertNotIn("band", distributed.inputs(connection, below, {}, time.time())["records"][0])

    def test_publication_is_validated_and_limited_to_tile_tasks(self) -> None:
        parent = self.enqueue()
        origin = self.make_running_tile(parent, 0, 0)
        with leader.connect(self.database) as connection:
            for bad in ({"packet": "nope", "bands": {}},
                        {"packet": self.packet, "bands": {"top": {"sha256": "b" * 64, "size": 1, "location": "x"}}},
                        {"packet": self.packet, "bands": {"bottom": {"sha256": "short", "size": 1, "location": "x"}}},
                        {"packet": self.packet, "bands": {"bottom": {"sha256": "b" * 64, "size": 0, "location": "x"}}}):
                with self.assertRaises(ValueError):
                    distributed.inputs(connection, origin, {"publish_bands": bad}, time.time())
            root = connection.execute("SELECT * FROM runs WHERE run_id=?", (parent,)).fetchone()
            with self.assertRaises(ValueError):
                distributed.inputs(connection, root, {"publish_bands": {"packet": self.packet, "bands": {}}}, time.time())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tile_bands").fetchone()[0], 0)

    def test_new_attempt_reusing_a_tile_keeps_its_bands(self) -> None:
        """Bands hang off the packet hash, so an imported tile needs no extra bookkeeping."""

        old = self.enqueue()
        origin = self.make_running_tile(old, 0, 0)
        self.publish(origin)
        self.complete(old, 0, 0, self.packet)
        with leader.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='failed',finished=? WHERE run_id=?", (time.time(), old))
        current = self.handler.dispatch_post("/v1/enqueue", {"specification": {
            "program": "dp_distributed", "arguments": {"p": self.p, "r": self.r, "tile_side": self.side, "threads": 1}},
            "rerun": True})["run_id"]
        with leader.connect(self.database) as connection:
            reused = connection.execute(
                "SELECT r.artifact_hash FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                "WHERE t.parent_run_id=? AND t.row=0 AND t.column=0", (current,)).fetchone()
            self.assertEqual(reused[0], self.packet)
            self.assertEqual({row[0] for row in connection.execute("SELECT kind FROM tile_bands WHERE packet_hash=?",
                                                                   (self.packet,))}, {"bottom", "right", "corner"})


class BandFallbackTests(unittest.TestCase):
    """Any trouble with a band sends the worker to the whole packet it was always offered."""

    def setUp(self) -> None:
        import distributed_solver
        self.module = distributed_solver
        self.p, self.r, self.side = 3, 7, 32
        self.target = tile(self.p, self.r, self.side, 1, 0)
        self.record = {"row": 0, "column": 0, "sha256": "a" * 64, "size": 10, "locations": ["x"],
                       "band": {"kind": "bottom", "sha256": "b" * 64, "size": 5, "locations": ["y"]}}
        self.calls = []
        self.addCleanup(setattr, distributed_solver, "REPORTER", distributed_solver.REPORTER)
        distributed_solver.REPORTER = None
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)

    def patched(self, band_error):
        module = self.module
        calls = self.calls

        def band(*arguments):
            calls.append("band")
            if band_error:
                raise band_error
            return "piece"

        def packet(*arguments):
            calls.append("packet")
            return Path("/packet")

        return (unittest.mock.patch.object(module, "acquire_band", band),
                unittest.mock.patch.object(module, "acquire", packet))

    def run_input(self, band_error=None):
        band, packet = self.patched(band_error)
        with band, packet:
            return self.module.acquire_input(self.record, None, self.p, self.r, self.side, self.target, self.cache)

    def test_band_is_preferred(self) -> None:
        self.assertEqual(self.run_input(), ("piece", "band", 5))
        self.assertEqual(self.calls, ["band"])

    def test_damaged_unreachable_or_mismatched_band_falls_back_to_the_packet(self) -> None:
        for error in (ValueError("band identity or layout mismatch"), OSError("peer unreachable"), KeyError("size")):
            self.calls.clear()
            source, mode, size = self.run_input(error)
            self.assertEqual((source, mode, size), (Path("/packet/values.bin"), "packet", 10))
            self.assertEqual(self.calls, ["band", "packet"])

    def test_a_stop_is_never_swallowed(self) -> None:
        with self.assertRaises(InterruptedError):
            self.run_input(InterruptedError("tile transfer stopped"))
        self.assertEqual(self.calls, ["band"])

    def test_descriptor_naming_the_wrong_kind_or_the_kill_switch_uses_the_packet(self) -> None:
        self.record["band"]["kind"] = "right"
        self.assertEqual(self.run_input()[1], "packet")
        self.assertEqual(self.calls, ["packet"])
        self.record["band"]["kind"] = "bottom"
        self.calls.clear()
        with unittest.mock.patch.dict(os.environ, {"KH_DP_BANDS": "0"}):
            self.assertEqual(self.run_input()[1], "packet")
        self.assertEqual(self.calls, ["packet"])


class BandClusterTests(unittest.TestCase):
    """Real leader, agents and C kernel: later tiles fetch bands, and the split still matches the reference."""

    def test_distributed_split_with_bands_matches_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cluster = Cluster(root)
            try:
                for name in ("a", "b", "c"):
                    cluster.worker(name, 1)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 3, "three workers")
                specification = {"program": "dp_distributed", "arguments": {
                    "p": 3, "r": 7, "tile_side": 32, "threads": 1, "artifact_format": "KHD1"}}
                queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": specification})

                def complete():
                    run = find_run(cluster.url, queued["run_id"])
                    if run["state"] == "failed":
                        raise AssertionError(run["error"])
                    return run if run["state"] == "complete" else None

                finished = wait_until(complete, "distributed DP with bands", timeout=120)
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
                    details = {}
                    for row, column, text in connection.execute(
                            "SELECT t.row,t.column,r.progress_details FROM distributed_tiles t "
                            "JOIN runs r ON r.run_id=t.child_run_id WHERE t.parent_run_id=?", (queued["run_id"],)):
                        details[(row, column)] = json.loads(text)
                    self.assertEqual(len(details), 9)
                    self.assertEqual(details[(0, 0)]["input_mode"], "none")
                    for key in ((1, 1), (2, 2), (1, 2), (2, 1)):
                        self.assertEqual(details[key]["input_mode"], "bands", f"tile {key}: {details[key]}")
                        self.assertGreater(details[key]["input_band_bytes"], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM tile_bands").fetchone()[0],
                                     sum(len(needed_bands(3, 7, 32, tile(3, 7, 32, *key))) for key in details))
                    for (digest,) in connection.execute("SELECT band_hash FROM tile_bands"):
                        self.assertGreaterEqual(connection.execute(
                            "SELECT COUNT(*) FROM replicas WHERE artifact_hash=?", (digest,)).fetchone()[0], 1)
                    packets = sum(size for (size,) in connection.execute(
                        "SELECT a.size FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id "
                        "JOIN artifacts a ON a.artifact_hash=r.artifact_hash WHERE t.parent_run_id=?", (queued["run_id"],)))
                    band_bytes = sum(size for (size,) in connection.execute(
                        "SELECT a.size FROM tile_bands b JOIN artifacts a ON a.artifact_hash=b.band_hash"))
                    self.assertLess(band_bytes, packets)
            finally:
                cluster.close()


# Empty invocation is descriptive and starts nothing.
if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test DP tile edge bands.\nExample: python3 tests/test_bands.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
