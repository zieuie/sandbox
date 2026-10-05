#!/usr/bin/env python3
"""Check disk measurement: the remote script, classification, the four-way split and the monitor."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import fixture
from fixture import NOW
import disk
import snapshot

GIB = 2**30


def allocated(path: Path) -> int:
    return os.stat(path).st_blocks * 512


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


class RemoteScriptTests(unittest.TestCase):
    """The script every machine runs: allocated bytes, once per inode, split by where they live."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.base = root / "king_hamming"
        self.deploy = self.base / "dp-1"
        self.blobs = self.deploy / "blobs"
        self.extra = root / "repository"
        self.digests = ["ab" + "c" * 62, "cd" + "e" * 62]
        self.files = [write(self.blobs / d[:2] / d[2:], 10_000) for d in self.digests]

    def run_script(self) -> dict:
        return disk.run_script([sys.executable, "-", str(self.base), str(self.blobs), str(self.extra)])

    def test_blobs_work_other_deployments_and_extras_are_separate(self) -> None:
        write(self.blobs / ".downloads" / "x.part", 5_000)            # in the store but not a blob
        write(self.blobs / "ab" / ".store-tmp", 5_000)                # a temporary file inside a shard
        scratch = write(self.deploy / "work" / "run" / "result.bin", 20_000)
        state = write(self.deploy / "agent.json", 3_000)
        old = write(self.base / "dp-old" / "blobs" / "11" / ("2" * 62), 7_000)
        repo = write(self.extra / "backup.tar", 9_000)
        raw = self.run_script()
        disk.check_raw(raw)
        self.assertEqual(sorted(raw["blobs"]), sorted([d, allocated(f)] for d, f in zip(self.digests, self.files)))
        self.assertEqual(raw["blobs_bytes"], sum(allocated(f) for f in self.files)
                         + allocated(self.blobs / ".downloads" / "x.part") + allocated(self.blobs / "ab" / ".store-tmp"))
        self.assertEqual(raw["work_bytes"], allocated(scratch))
        self.assertEqual(raw["deployment_bytes"], allocated(state))
        self.assertEqual(raw["other_deployments_bytes"], allocated(old))
        self.assertEqual(raw["extra_bytes"], allocated(repo))
        status = os.statvfs(self.base)
        self.assertEqual((raw["size"], raw["available"]), (status.f_blocks * status.f_frsize, status.f_bavail * status.f_frsize))
        self.assertEqual(raw["errors"], 0)

    def test_a_hard_link_is_counted_once_and_attributed_to_the_blob_store(self) -> None:
        link = self.deploy / "work" / ".dependency-cache" / "packet"
        link.parent.mkdir(parents=True)
        os.link(self.files[0], link)
        raw = self.run_script()
        self.assertEqual(raw["work_bytes"], 0)
        self.assertEqual(raw["blobs_bytes"], sum(allocated(f) for f in self.files))

    def test_a_missing_extra_directory_is_ignored(self) -> None:
        self.assertEqual(self.run_script()["extra_bytes"], 0)

    def test_malformed_answers_are_refused(self) -> None:
        good = self.run_script()
        disk.check_raw(good)
        for change in ({"size": 0}, {"available": good["size"] + 1}, {"blobs_bytes": -1}, {"blobs": [["short", 1]]},
                       {"blobs": "no"}, {"errors": "0"}):
            with self.assertRaises(disk.MeasureError, msg=str(change)):
                disk.check_raw({**good, **change})
        with self.assertRaises(disk.MeasureError):
            disk.check_raw([])

    def test_a_failing_command_reports_why(self) -> None:
        with self.assertRaises(disk.MeasureError) as caught:
            disk.run_script([sys.executable, "-", "/does/not/exist", "/does/not/exist/blobs"])
        self.assertIn("No such file", str(caught.exception))

    def test_only_a_registered_storage_path_is_ever_sent_over_ssh(self) -> None:
        runner = disk.ssh_runner("192.168.4.151", ())
        for root in ("/tmp", "/home/x/.local/share/king_hamming/dp-1/blobs; rm -rf /", "/home/x/../etc/blobs", ""):
            with self.assertRaises(disk.MeasureError, msg=root):
                runner("192.168.4.101", root)
        self.assertTrue(disk.valid_root("/home/zieuie/.local/share/king_hamming/dp-1065dc8a/blobs"))


class ClassifyTests(unittest.TestCase):
    """Packets and bands are tiles; a tile shared by attempts takes the best state of its roots."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = fixture.database(Path(self.directory.name) / "live")
        c = self.connection
        for name in ("a", "b", "c", "d"):
            fixture.node(c, name, f"http://{name}:9000", "0,1", NOW)
        spec = {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7}}
        for root, state in (("done", "complete"), ("dead", "failed"), ("live", "waiting")):
            fixture.run(c, root, spec, state)
        digests = {"fin": "1" * 64, "unf": "2" * 64, "bad": "3" * 64, "shared": "4" * 64}
        for child, digest, parents in (("t1", digests["fin"], ["done"]), ("t2", digests["unf"], ["live"]),
                                        ("t3", digests["bad"], ["dead"]), ("t4", digests["shared"], ["dead", "live"])):
            for index, parent in enumerate(parents):
                fixture.run(c, f"{child}-{index}", fixture.tile_spec(0, 0, parent), "complete", parent=parent,
                            artifact_hash=digest)
        # Copies: fin on 4 nodes (target 2), unf on 2, bad on 1; shared on 3.
        for digest, nodes in ((digests["fin"], "abcd"), (digests["unf"], "ab"), (digests["bad"], "a"), (digests["shared"], "abc")):
            fixture.artifact(c, digest, list(nodes))
            c.execute("UPDATE artifacts SET size=1000 WHERE artifact_hash=?", (digest,))
        self.digests = digests
        self.band = "5" * 64
        fixture.artifact(c, self.band, ["a"])
        c.execute("UPDATE artifacts SET size=100 WHERE artifact_hash=?", (self.band,))
        c.execute("INSERT INTO tile_bands(packet_hash,kind,band_hash) VALUES(?,?,?)", (digests["fin"], "bottom", self.band))
        # An artifact that is not a tile never counts.
        fixture.artifact(c, "9" * 64, ["a", "b", "c", "d"])
        c.commit()

    def test_groups_and_bands(self) -> None:
        index = disk.tile_index(self.connection)
        groups = {digest: group for digest, (group, _) in index["groups"].items()}
        self.assertEqual(groups[self.digests["fin"]], "finished")
        self.assertEqual(groups[self.digests["unf"]], "unfinished")
        self.assertEqual(groups[self.digests["bad"]], "failed")
        self.assertEqual(groups[self.digests["shared"]], "unfinished")  # live beats failed
        self.assertEqual(groups[self.band], "finished")                 # a band follows its packet
        self.assertNotIn("9" * 64, groups)
        self.assertEqual(index["groups"][self.digests["fin"]][1], (5, 3))

    def test_copy_statistics(self) -> None:
        index = disk.tile_index(self.connection)
        # target is 2 in the fixture: fin 4, unf 2, bad 1, shared 3, band 1.
        self.assertEqual(index["tiles"], 5)
        self.assertEqual(index["unique_bytes"], 4 * 1000 + 100)
        self.assertEqual(index["excess_bytes"], 1000 * (4 - 2) + 1000 * (3 - 2))
        self.assertEqual(index["average_copies"], round((4 + 2 + 1 + 3 + 1) / 5, 2))

    def test_a_leader_without_bands_still_classifies(self) -> None:
        self.connection.execute("DROP TABLE tile_bands")
        self.assertNotIn(self.band, disk.tile_index(self.connection)["groups"])


class BreakdownTests(unittest.TestCase):
    RAW = {"blobs_bytes": 150 * GIB, "work_bytes": 20 * GIB, "deployment_bytes": 1 * GIB,
           "other_deployments_bytes": 4 * GIB, "extra_bytes": 0, "size": 400 * GIB, "free": 70 * GIB,
           "available": 50 * GIB, "errors": 0, "blobs": []}

    def groups(self):
        return {"a" * 64: ("finished", (13, 9)), "b" * 64: ("unfinished", (13, 9)),
                "c" * 64: ("failed", (7, 11)), "d" * 64: ("unfinished", (11, 9))}

    def raw(self, **changes):
        return {**self.RAW, **changes, "blobs": [["a" * 64, 60 * GIB], ["b" * 64, 50 * GIB], ["c" * 64, 5 * GIB],
                                                  ["d" * 64, 20 * GIB], ["9" * 64, 3 * GIB]]}

    def test_four_categories_add_up_to_the_disk(self) -> None:
        result = disk.breakdown(self.raw(), self.groups(), 123.0)
        self.assertEqual(result["tiles"], 135 * GIB)
        self.assertEqual(result["tiles_by_group"], {"finished": 60 * GIB, "unfinished": 70 * GIB, "failed": 5 * GIB})
        # other = everything king_hamming keeps (175) minus tiles (135); free is what a user can still write.
        self.assertEqual(result["other"], 40 * GIB)
        self.assertEqual(result["free"], 50 * GIB)
        self.assertEqual(result["unrelated"], 400 * GIB - 50 * GIB - 135 * GIB - 40 * GIB)
        self.assertEqual(sum(result[key] for key in ("tiles", "other", "unrelated", "free")), result["size"])

    def test_root_reserve_lands_in_unrelated_and_is_reported(self) -> None:
        self.assertEqual(disk.breakdown(self.raw(), self.groups(), 0)["reserved"], 20 * GIB)

    def test_detail_for_the_tooltip(self) -> None:
        result = disk.breakdown(self.raw(), self.groups(), 0)
        self.assertEqual(result["top_fields"], [[13, 9, 110 * GIB], [11, 9, 20 * GIB], [7, 11, 5 * GIB]])
        self.assertEqual(result["other_parts"], {"blobs": 15 * GIB, "scratch": 20 * GIB, "deployments": 5 * GIB, "repository": 0})
        self.assertEqual(result["unlisted_blobs"], 150 * GIB - 138 * GIB)

    def test_inconsistent_numbers_never_go_negative(self) -> None:
        result = disk.breakdown(self.raw(size=100 * GIB, available=90 * GIB, free=90 * GIB), self.groups(), 0)
        self.assertEqual(result["unrelated"], 0)
        self.assertGreaterEqual(result["other"], 0)

    def test_summary_adds_machines_and_names_what_could_be_reclaimed(self) -> None:
        one = disk.breakdown(self.raw(), self.groups(), 0)
        summary = disk.summarize({"x": one, "y": one, "stale": {"error": "timed out"}},
                                 {"excess_bytes": 7, "average_copies": 6.5, "unique_bytes": 9})
        self.assertEqual(summary["total"]["size"], 800 * GIB)
        self.assertEqual(summary["total"]["machines"], 2)
        self.assertEqual(summary["reclaimable"], {"finished_tiles": 120 * GIB, "scratch": 40 * GIB, "excess_copies": 7})
        self.assertEqual((summary["average_copies"], summary["target_copies"]), (6.5, 3))
        self.assertIsNone(disk.summarize({"x": one}, None)["reclaimable"]["excess_copies"])


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.deployments = root / "deployments"
        fixture.live_campaign(self.deployments)
        self.state = root / "state"
        self.now = NOW
        self.calls: list[tuple[str, str]] = []
        self.answers: dict[str, object] = {}
        self.changes = 0
        self.monitor = self.make()

    def make(self):
        return disk.DiskMonitor(
            self.state, self.deployments, "live", ["192.168.4.101", "192.168.4.151", "192.168.4.152"], self.runner,
            interval=1800, clock=lambda: self.now, on_change=self.changed)

    def changed(self) -> None:
        self.changes += 1

    def runner(self, host: str, root: str) -> dict:
        self.calls.append((host, root))
        answer = self.answers.get(host)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else BreakdownTests().raw()

    def test_measures_every_known_host_and_reports_the_rest(self) -> None:
        self.monitor.measure()
        view = self.monitor.view()
        self.assertEqual([host for host, _ in self.calls], ["192.168.4.101", "192.168.4.151"])
        self.assertEqual(view["hosts"]["192.168.4.101"]["size"], 400 * GIB)
        self.assertEqual(view["hosts"]["192.168.4.152"]["error"], "not registered with the leader")
        self.assertEqual(view["cluster"]["total"]["machines"], 2)
        self.assertEqual((view["measured_at"], view["running"]), (self.now, False))
        self.assertEqual(self.changes, 2)  # the snapshot is invalidated when it starts and when it ends

    def test_gawain_is_named_and_included_in_fleet_disk_measurements(self) -> None:
        with sqlite3.connect(self.deployments / "live" / "leader.sqlite") as connection:
            fixture.node(connection, "dp-156", "http://192.168.4.156:9000", "0,1,2,3", NOW - 5)
        monitor = disk.DiskMonitor(
            self.state, self.deployments, "live", list(snapshot.HOST_NAMES), self.runner,
            interval=1800, clock=lambda: self.now)
        monitor.measure()
        self.assertEqual(snapshot.HOST_NAMES["192.168.4.156"], "gawain")
        self.assertIn("192.168.4.156", [host for host, _ in self.calls])
        self.assertEqual(monitor.view()["hosts"]["192.168.4.156"]["size"], 400 * GIB)
        cards = snapshot.Snapshots(self.deployments, "live", clock=lambda: NOW).get()[0]["fleet"]["nodes"]
        self.assertEqual(next(card["hostname"] for card in cards if card["host"] == "192.168.4.156"),
                         "gawain")

    def test_new_host_prompts_disk_refresh_after_dashboard_restart(self) -> None:
        self.monitor.measure()
        enlarged = disk.DiskMonitor(
            self.state, self.deployments, "live", [*self.monitor.hosts, "192.168.4.156"],
            self.runner, interval=1800, clock=lambda: self.now)

        class Observed(Exception):
            pass

        def observe(delay):
            self.assertEqual(delay, 5.0)
            raise Observed()

        with patch.object(enlarged.wake, "wait", side_effect=observe):
            with self.assertRaises(Observed):
                enlarged.loop()

    def test_a_retired_machine_is_not_measured_and_leaves_the_totals(self) -> None:
        self.monitor.measure()
        self.assertEqual(self.monitor.view()["cluster"]["total"]["machines"], 2)
        with sqlite3.connect(self.deployments / "live" / "leader.sqlite") as connection:
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('retired_nodes','[\"dp-101\"]')")
        self.calls.clear()
        self.monitor.measure()
        view = self.monitor.view()
        self.assertEqual([host for host, _ in self.calls], ["192.168.4.151"])
        self.assertNotIn("192.168.4.101", view["hosts"])
        self.assertEqual(view["cluster"]["total"]["machines"], 1)

    def test_a_failed_host_keeps_its_last_numbers_and_says_why(self) -> None:
        self.monitor.measure()
        self.now += 1800
        self.answers["192.168.4.101"] = disk.MeasureError("timed out")
        self.answers["192.168.4.151"] = RuntimeError("boom")
        self.monitor.measure()
        host = self.monitor.view()["hosts"]["192.168.4.101"]
        self.assertEqual((host["error"], host["size"]), ("timed out", 400 * GIB))
        self.assertEqual(host["measured_at"], NOW)
        self.assertEqual(host["error_at"], NOW + 1800)
        self.assertEqual(self.monitor.view()["hosts"]["192.168.4.151"]["error"], "RuntimeError: boom")
        self.assertEqual(self.monitor.view()["measured_at"], NOW)  # nothing succeeded this round

    def test_results_survive_a_restart(self) -> None:
        self.monitor.measure()
        again = self.make()
        self.assertEqual(again.view()["hosts"], self.monitor.view()["hosts"])
        self.assertEqual(again.view()["measured_at"], NOW)
        (self.state / "disk.json").write_text("{not json")
        self.assertEqual(self.make().view()["hosts"], {})

    def test_requests_are_rate_limited_and_never_overlap(self) -> None:
        self.assertEqual(self.monitor.request(), {"started": True})
        self.assertTrue(self.monitor.wake.is_set())
        refused = self.monitor.request()
        self.assertFalse(refused["started"])
        self.assertGreater(refused["retry_after"], 0)
        self.now += disk.MIN_GAP + 1
        self.assertTrue(self.monitor.request()["started"])
        self.monitor.running = True
        self.now += disk.MIN_GAP + 1
        self.assertEqual(self.monitor.request()["reason"], "a measurement is already running")

    def test_an_unreadable_leader_database_marks_every_host(self) -> None:
        for name in ("leader.sqlite", "leader.sqlite-wal", "leader.sqlite-shm"):
            (self.deployments / "live" / name).unlink(missing_ok=True)
        self.monitor.measure()
        errors = {item["error"].split(":")[0] for item in self.monitor.view()["hosts"].values()}
        self.assertEqual(errors, {"leader database"})
        self.assertEqual(self.calls, [])

    def test_the_snapshot_shows_the_latest_result_and_measures_nothing(self) -> None:
        snapshots = snapshot.Snapshots(self.deployments, "live", clock=lambda: NOW)
        self.assertIsNone(snapshots.get()[0]["fleet"]["disk"])
        self.assertNotIn("disk", snapshots.get()[0]["fleet"]["nodes"][0])
        self.monitor.measure()
        self.calls.clear()
        snapshots.disk = self.monitor
        snapshots.invalidate()
        fleet = snapshots.get()[0]["fleet"]
        self.assertEqual(self.calls, [])
        self.assertEqual(fleet["disk"]["cluster"]["total"]["machines"], 2)
        by_host = {node["host"]: node for node in fleet["nodes"]}
        # Only the fixture's two tile packets are tiles: 60 + 50 GiB of the canned listing.
        self.assertEqual(by_host["192.168.4.101"]["disk"]["tiles"], 110 * GIB)
        self.assertIsNone(by_host["192.168.4.108"]["disk"])

    def test_a_disabled_monitor_leaves_the_fleet_unchanged(self) -> None:
        snapshots = snapshot.Snapshots(self.deployments, "live", clock=lambda: NOW)
        fleet = snapshots.get()[0]["fleet"]
        self.assertIsNone(fleet["disk"])


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        fixture.live_campaign(root / "deployments")
        cls.monitor = disk.DiskMonitor(root / "state", root / "deployments", "live", ["192.168.4.101"],
                                       lambda host, path: {}, min_gap=3600)
        cls.httpd, cls.base, _ = fixture.serve(root / "deployments", root / "state", disk=cls.monitor)
        cls.operator = fixture.Client(cls.base)
        assert cls.operator.login("tester", fixture.PASSWORD)[0] == 200
        cls.viewer = fixture.Client(cls.base)
        assert cls.viewer.login("guest", fixture.PASSWORD + "!")[0] == 200
        cls.bare = tempfile.TemporaryDirectory()
        fixture.live_campaign(Path(cls.bare.name) / "deployments")
        cls.httpd2, cls.base2, _ = fixture.serve(Path(cls.bare.name) / "deployments", Path(cls.bare.name) / "state")
        cls.without = fixture.Client(cls.base2)
        assert cls.without.login("tester", fixture.PASSWORD)[0] == 200

    @classmethod
    def tearDownClass(cls) -> None:
        for httpd in (cls.httpd, cls.httpd2):
            httpd.shutdown()
            httpd.server_close()
        cls.directory.cleanup()
        cls.bare.cleanup()

    def test_only_operators_can_ask_for_a_measurement_and_only_once_in_a_while(self) -> None:
        self.assertEqual(self.viewer.json("/api/disk/measure", "POST", {})[0], 403)
        status, _, body = self.operator.json("/api/disk/measure", "POST", {})
        self.assertEqual((status, body), (200, {"started": True}))
        status, headers, body = self.operator.json("/api/disk/measure", "POST", {})
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)
        self.assertEqual(self.operator.request("/api/disk/measure")[0], 404)  # POST only

    def test_the_request_is_audited_and_refused_without_a_monitor(self) -> None:
        entries = self.operator.json("/api/audit")[2]["entries"]
        self.assertTrue(any(entry["action"] == "disk-measure" for entry in entries))
        self.assertEqual(self.without.json("/api/disk/measure", "POST", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
