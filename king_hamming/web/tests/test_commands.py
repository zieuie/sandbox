#!/usr/bin/env python3
"""Check command previews, refusals, staleness, re-confirmation, execution and jobs."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

import fixture
from fixture import NOW  # noqa: F401  (fixture sets up the import path)
from commands import CommandError, CommandService, Context  # noqa: E402
from audit import Audit  # noqa: E402
from jobs import Jobs  # noqa: E402
import snapshot  # noqa: E402


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.deployments = root / "deployments"
        fixture.live_campaign(self.deployments)
        fixture.feeder_state(self.deployments)
        self.leader = fixture.FakeLeader()
        fixture.command_campaign(self.deployments, self.leader.url)
        self.state = root / "state"
        self.launcher = fixture.fake_launcher(root, delay=0.5)
        self.httpd, self.base, self.auth = fixture.serve(self.deployments, self.state, self.launcher)
        self.client = fixture.Client(self.base)
        self.client.login("tester", fixture.PASSWORD)
        self.database = self.deployments / "live" / "leader.sqlite"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.leader.close()
        self.directory.cleanup()

    def preview(self, name, params=None, client=None):
        return (client or self.client).json("/api/command/preview", "POST", {"name": name, "params": params or {}})

    def run_command(self, name, params=None, confirm=None, fingerprint=None):
        if fingerprint is None:
            fingerprint = self.preview(name, params)[2]["fingerprint"]
        return self.client.json("/api/command/run", "POST", {"name": name, "params": params or {},
                                                             "fingerprint": fingerprint, "confirm": confirm})

    def audit(self):
        return [json.loads(line) for line in (self.state / "audit.jsonl").read_text().splitlines()
                if '"command"' in line]

    def test_viewers_cannot_preview_or_run(self) -> None:
        guest = fixture.Client(self.base)
        guest.login("guest", fixture.PASSWORD + "!")
        self.assertEqual(self.preview("dispatch.stop", client=guest)[0], 403)
        self.assertEqual(guest.request("/api/command/run", "POST", {"name": "dispatch.stop"})[0], 403)
        self.assertEqual(self.client.request("/api/command/run", "POST", {"name": "dispatch.stop"},
                                             csrf=False)[0], 403)

    def test_stop_dispatch_needs_typed_confirmation(self) -> None:
        status, _, preview = self.preview("dispatch.stop")
        self.assertEqual((status, preview["confirm_text"]), (200, "stop"))
        self.assertEqual(preview["warnings"], ["1 running lease(s) will be asked to stop."])
        status, _, reply = self.run_command("dispatch.stop", confirm="nope")
        self.assertEqual(status, 400)
        self.assertEqual(self.leader.calls, [])
        status, _, reply = self.run_command("dispatch.stop", confirm="stop")
        self.assertEqual((status, reply["ok"]), (200, True))
        self.assertEqual(self.leader.calls, [("/v1/control", {"state": "stopped"})])
        entry = self.audit()[-1]
        self.assertEqual((entry["command"], entry["outcome"], entry["user"]), ("dispatch.stop", "ok", "tester"))

    def test_dp_tiles_on_cpus_is_off_by_default_and_toggles(self) -> None:
        self.assertFalse(self.client.json("/api/jobs")[2]["dispatch"]["cpu_fallback"])
        self.assertEqual(self.preview("tiles.cpu_fallback", {"allow": False})[2]["blockers"],
                         ["DP tiles on CPUs are already off."])
        status, _, reply = self.run_command("tiles.cpu_fallback", {"allow": True})
        self.assertEqual((status, reply["result"]), (200, {"cpu_fallback": True}))
        self.assertTrue(self.client.json("/api/jobs")[2]["dispatch"]["cpu_fallback"])
        status, _, reply = self.run_command("tiles.cpu_fallback", {"allow": False})
        self.assertEqual(reply["result"], {"cpu_fallback": False})
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT value FROM settings WHERE key='dp_cpu_fallback'").fetchone()[0], "0")
        self.assertEqual(self.preview("tiles.cpu_fallback", {"allow": "yes"})[0], 400)

    def test_drain_stops_new_work_without_asking_running_work_to_stop(self) -> None:
        status, _, preview = self.preview("dispatch.drain")
        self.assertEqual((status, preview["confirm_text"], preview["blockers"]), (200, None, []))
        self.assertEqual(preview["warnings"], ["1 run(s) are in flight; they will finish on their own."])
        status, _, reply = self.run_command("dispatch.drain")
        self.assertEqual((status, reply["result"]), (200, {"campaign_state": "stopped", "still_running": 1}))
        self.assertEqual(self.leader.calls, [])        # the leader's stop route would abort running tiles
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute(
                "SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0], "stopped")
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM runs WHERE state='running' AND stop_requested=1").fetchone()[0], 0)
        self.assertEqual(self.preview("dispatch.drain")[2]["blockers"], ["Dispatch is already stopped."])
        status, _, jobs = self.client.json("/api/jobs")
        self.assertEqual(status, 200)
        self.assertTrue(jobs["dispatch"]["draining"])
        self.assertFalse(jobs["dispatch"]["idle"])
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='complete' WHERE state IN ('running','stopping')")
        self.assertTrue(self.client.json("/api/jobs")[2]["dispatch"]["idle"])

    def test_stale_preview_is_refused_with_a_fresh_one(self) -> None:
        fingerprint = self.preview("dispatch.stop")[2]["fingerprint"]
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET state='complete' WHERE run_id='t10'")
        status, _, reply = self.run_command("dispatch.stop", confirm="stop", fingerprint=fingerprint)
        self.assertEqual(status, 409)
        self.assertNotEqual(reply["preview"]["fingerprint"], fingerprint)
        self.assertEqual(reply["preview"]["warnings"], [])
        self.assertEqual(self.leader.calls, [])

    def test_blockers_refuse(self) -> None:
        status, _, preview = self.preview("run.cancel", {"run_id": "t10"})
        self.assertIn("unable to finish", preview["blockers"][0])
        status, _, reply = self.run_command("run.cancel", {"run_id": "t10"})
        self.assertEqual(status, 409)
        self.assertEqual(self.preview("dispatch.resume")[2]["blockers"], ["Dispatch is already running."])
        self.assertEqual(self.preview("run.pause", {"run_id": "missing"})[0], 404)
        self.assertEqual(self.preview("nonsense")[0], 404)

    def test_pause_tile_and_priority(self) -> None:
        status, _, reply = self.run_command("run.pause", {"run_id": "t10"})
        self.assertEqual(status, 200)
        self.assertEqual(self.leader.calls[-1], ("/v1/run-command", {"run_id": "t10", "action": "pause"}))
        status, _, reply = self.run_command("run.priority", {"run_id": "t10", "priority": "7"})
        self.assertEqual(status, 200)
        self.assertEqual(self.leader.calls[-1][1], {"run_id": "t10", "action": "reprioritize", "priority": 7})
        self.assertEqual(self.preview("run.priority", {"run_id": "t10", "priority": 5000})[0], 400)

    def test_leader_refusal_is_reported_and_audited(self) -> None:
        self.leader.refuse.add("/v1/run-command")
        status, _, reply = self.run_command("run.pause", {"run_id": "t10"})
        self.assertEqual(status, 409)
        self.assertIn("terminal runs cannot be controlled", reply["error"])
        self.assertEqual(self.audit()[-1]["outcome"], "failed")

    def test_restart_root_cancels_live_tiles_and_resubmits(self) -> None:
        status, _, preview = self.preview("root.restart", {"run_id": "root-5-3"})
        self.assertEqual((preview["confirm_text"], preview["reauth"]), ("5^3", True))
        status, _, reply = self.run_command("root.restart", {"run_id": "root-5-3"}, confirm="5^3")
        self.assertEqual(status, 200, reply)
        paths = [(path, body.get("action"), body.get("run_id")) for path, body in self.leader.calls]
        self.assertEqual(paths[:2], [("/v1/run-command", "cancel", "root-5-3"),
                                     ("/v1/run-command", "cancel", "t10")])
        self.assertEqual(self.leader.calls[2][0], "/v1/enqueue")
        self.assertTrue(self.leader.calls[2][1]["rerun"])
        manifest = json.loads((self.deployments / "live" / "manifest.json").read_text())
        self.assertEqual(manifest["entries"][-1]["run_id"], "new-3")

    def test_reauth_required_for_risky_commands(self) -> None:
        audit = Audit(self.state)
        service = CommandService(Context(self.deployments, "live", snapshot.Snapshots(self.deployments, "live"),
                                         Jobs(self.state / "jobs2")), audit)
        plan = service.preview("root.restart", {"run_id": "root-5-3"})
        with self.assertRaises(CommandError) as raised:
            service.run("root.restart", {"run_id": "root-5-3"}, plan["fingerprint"], "5^3",
                        {"user": "tester"}, "127.0.0.1", recently_authenticated=False)
        self.assertEqual((raised.exception.status, raised.exception.extra), (403, {"reauth_required": True}))
        self.assertEqual(self.leader.calls, [])

    def test_feeder_settings(self) -> None:
        status, _, preview = self.preview("feeder.settings", {"changes": {"max_visits": "3e14"}})
        self.assertEqual(preview["changes"], [{"label": "max_visits", "before": 30_000_000_000_000,
                                               "after": 300_000_000_000_000}])
        self.assertTrue(preview["reauth"])
        bad = self.preview("feeder.settings", {"changes": {"target_dp_roots": 50}})[2]
        self.assertIn("would reject", bad["blockers"][0])
        self.assertEqual(self.preview("feeder.settings", {"changes": {"nonsense": 1}})[0], 400)
        self.assertEqual(self.preview("feeder.settings", {"changes": {"max_visits": "1.5"}})[0], 400)
        off = self.preview("feeder.settings", {"changes": {"new_dp_fields": 0}})[2]
        self.assertEqual(off["changes"], [{"label": "new_dp_fields", "before": None, "after": 0}])
        self.assertEqual(off["blockers"], [])
        self.assertEqual(self.preview("feeder.settings", {"changes": {"new_dp_fields": 2}})[0], 400)
        status, _, reply = self.run_command("feeder.settings", {"changes": {"max_visits": "3e14"}})
        self.assertEqual(status, 200, reply)
        pipeline = json.loads((self.deployments / "live" / "pipeline.json").read_text())
        self.assertEqual(pipeline["settings"]["max_visits"], 300_000_000_000_000)
        self.assertIn("fields", pipeline)  # the rest of the file is untouched

    def test_retry_given_up_field(self) -> None:
        status, _, preview = self.preview("feeder.retry", {"p": 97, "r": 3})
        self.assertEqual(len(preview["items"]), 3)
        self.assertTrue(any("tile timeout" in warning for warning in preview["warnings"]))
        self.assertTrue(any("max_dp_attempts" in warning for warning in preview["warnings"]))
        status, _, reply = self.run_command("feeder.retry", {"p": 97, "r": 3})
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.leader.calls[-1][1]["rerun"], True)
        manifest = json.loads((self.deployments / "live" / "manifest.json").read_text())
        self.assertEqual([entry["run_id"] for entry in manifest["entries"]][-1], "new-1")
        # The new attempt is now in the manifest, so the preview the operator saw is stale.
        self.assertEqual(self.preview("feeder.retry", {"p": 5, "r": 3})[2]["blockers"],
                         ["An attempt for this field is still active."])

    def test_extend_runs_the_launcher_under_the_feeder_lock(self) -> None:
        status, _, preview = self.preview("feeder.extend", {"max_visits": "1e9", "limit": "2"})
        self.assertEqual(len(preview["items"]), 2)
        status, _, reply = self.run_command("feeder.extend", {"max_visits": "1e9", "limit": "2"})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["result"], {"added_calculations": 0})

    def test_process_job_lifecycle(self) -> None:
        status, _, preview = self.preview("process.ensure_feeder")
        self.assertEqual((preview["job"], preview["blockers"]), (True, []))  # recorded feeder pid is dead
        status, _, reply = self.run_command("process.ensure_feeder")
        self.assertEqual(status, 200, reply)
        job_id = reply["job"]
        self.assertIn("Another job is running",
                      " ".join(self.preview("process.upgrade_workers")[2]["blockers"]))
        for _ in range(100):
            jobs = self.client.json("/api/jobs")[2]["jobs"]
            if jobs[0]["status"] != "running":
                break
            time.sleep(0.1)
        job = jobs[0]
        self.assertEqual((job["id"], job["status"], job["exit_code"], job["user"]), (job_id, "ok", 0, "tester"))
        self.assertIn("fake launcher: --state", job["tail"])
        self.assertIn("ensure-feeder", job["tail"])

    def test_submit_new_field(self) -> None:
        status, _, preview = self.preview("field.submit", {"p": "7", "r": "3", "priority": "4"})
        self.assertEqual((status, preview["blockers"], preview["confirm_text"]), (200, [], None))
        tiles = next(change["after"] for change in preview["changes"] if change["label"] == "Tiles")
        self.assertIn("of side 512", tiles)
        status, _, reply = self.run_command("field.submit", {"p": "7", "r": "3", "priority": "4"})
        self.assertEqual(status, 200, reply)
        path, body = self.leader.calls[-1]
        self.assertEqual((path, body["priority"], body["specification"]["program"]), ("/v1/enqueue", 4, "dp_distributed"))
        arguments = body["specification"]["arguments"]
        self.assertEqual((arguments["p"], arguments["r"], arguments["artifact_format"]), (7, 3, "KHD1"))
        manifest = json.loads((self.deployments / "live" / "manifest.json").read_text())
        self.assertEqual(manifest["entries"][-1]["specification"], body["specification"])
        # Now it exists, so a second submission is refused.
        self.assertIn("already exists", self.preview("field.submit", {"p": 7, "r": 3})[2]["blockers"][0])

    def test_submit_validation_and_big_fields(self) -> None:
        self.assertEqual(self.preview("field.submit", {"p": 11, "r": 8})[0], 400)
        self.assertEqual(self.preview("field.submit", {"p": 12, "r": 3})[0], 400)
        self.assertIn("already exists", self.preview("field.submit", {"p": 5, "r": 3})[2]["blockers"][0])
        big = self.preview("field.submit", {"p": 11, "r": 9})[2]
        self.assertEqual((big["blockers"], big["confirm_text"]), ([], "11^9"))
        self.assertIn("79 × 79 = 6,241 of side 2048",
                      next(change["after"] for change in big["changes"] if change["label"] == "Tiles"))
        self.assertTrue(any("matching field limit" in warning for warning in big["warnings"]))
        self.assertIn("No tile side", self.preview("field.submit", {"p": 127, "r": 3})[2]["blockers"][0])
        forced = self.preview("field.submit", {"p": 11, "r": 9, "tile_side": 512})[2]
        self.assertIn("No tile side up to 512", forced["blockers"][0])

    def live_nodes(self, free: int) -> None:
        """Make the fixture's nodes report `free` bytes just now (the preview ignores stale heartbeats)."""
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=?,storage_free_bytes=?", (time.time(), free))

    def test_submit_preview_shows_tile_storage_and_free_disk(self) -> None:
        self.live_nodes(500 * 1024**3)
        preview = self.preview("field.submit", {"p": 7, "r": 3})[2]
        changes = {change["label"]: change["after"] for change in preview["changes"]}
        self.assertIn("at 3 copies", changes["Tile storage"])
        self.assertIn("bytes per DP cell", changes["Tile storage"])
        self.assertIn("GiB above each machine's 10 GiB floor", changes["Free disk"])
        self.assertEqual(preview["blockers"], [])

    def test_a_field_that_cannot_fit_in_the_free_disk_is_refused(self) -> None:
        self.live_nodes(11 * 1024**3)                 # one GiB per machine above the floor
        big = self.preview("field.submit", {"p": 11, "r": 9})[2]
        self.assertTrue(any("tile storage but only about" in blocker for blocker in big["blockers"]), big["blockers"])
        self.assertIn("item 22", big["blockers"][0])       # names the automatic deletion that may make room

    def test_a_field_that_would_use_most_of_the_free_disk_is_warned(self) -> None:
        # 11^9 needs ~97 GB at the fallback rate; three machines with 50 GB usable each is enough but tight.
        self.live_nodes(10 * 1024**3 + 50 * 1000**3)
        big = self.preview("field.submit", {"p": 11, "r": 9})[2]
        self.assertEqual(big["blockers"], [])
        self.assertTrue(any("of the disk space that is free" in warning for warning in big["warnings"]), big["warnings"])

    def test_unknown_free_disk_is_a_warning_not_a_refusal(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE nodes SET last_heartbeat=0")
        preview = self.preview("field.submit", {"p": 7, "r": 3})[2]
        self.assertEqual(preview["blockers"], [])
        self.assertTrue(any("Free disk is not known" in warning for warning in preview["warnings"]))

    def test_a_machine_that_never_reported_free_space_is_not_counted(self) -> None:
        self.live_nodes(-1)
        preview = self.preview("field.submit", {"p": 7, "r": 3})[2]
        self.assertTrue(any("Free disk is not known" in warning for warning in preview["warnings"]))

    def test_fields_in_progress_reserve_their_remaining_storage(self) -> None:
        self.live_nodes(10 * 1024**3 + 200 * 1000**3)              # 600 GB usable across three machines
        self.assertEqual(self.preview("field.submit", {"p": 11, "r": 9})[2]["blockers"], [])
        with sqlite3.connect(self.database) as connection:         # a paused 13^9 still needs ~520 GB
            fixture.run(connection, "big-13-9", {"program": "dp_distributed", "arguments": {"p": 13, "r": 9, "tile_side": 4096}},
                        "paused", progress_total=100, progress_done=0)
        after = self.preview("field.submit", {"p": 11, "r": 9})[2]
        self.assertTrue(any("tile storage but only about" in blocker for blocker in after["blockers"]), after)
        free = next(change["after"] for change in after["changes"] if change["label"] == "Free disk")
        self.assertIn("still needed by fields in progress", free)
        # Once that field is mostly done, most of its reservation is released.
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE runs SET progress_done=95 WHERE run_id='big-13-9'")
        self.assertEqual(self.preview("field.submit", {"p": 11, "r": 9})[2]["blockers"], [])

    def test_storage_per_cell_is_measured_from_finished_fields(self) -> None:
        import commands
        from dp_solver.scheduling import dp_estimate
        cells = dp_estimate({"arguments": {"p": 5, "r": 3}})["state_bytes"] // 12
        with sqlite3.connect(self.database) as connection:
            fixture.run(connection, "done", {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 7}}, "complete")
            for index, size in enumerate((1500, 2500)):
                digest = f"{index}" * 64
                fixture.run(connection, f"done-t{index}", fixture.tile_spec(index, 0, "done"), "complete", parent="done",
                            artifact_hash=digest)
                connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,?)",
                                   (digest, NOW, size))
            self.assertAlmostEqual(commands.storage_per_cell(connection), 4000 / cells)

    def test_cancel_running_job(self) -> None:
        from jobs import Jobs
        slow_directory = Path(self.directory.name) / "slow"
        slow_directory.mkdir()
        slow = fixture.fake_launcher(slow_directory, delay=60)
        store = Jobs(self.state / "jobs")  # the same job store the server uses
        record = store.start("upgrade-workers", "Upgrade workers", ["python3", str(slow)], "tester",
                             cwd=Path(self.directory.name))
        for _ in range(100):
            if (store.record(record["id"]) or {}).get("child_pid"):
                break
            time.sleep(0.05)
        preview = self.preview("process.cancel_job", {"job_id": record["id"]})[2]
        self.assertEqual((preview["blockers"], preview["confirm_text"]), ([], "stop job"))
        self.assertIn("partway", preview["warnings"][0])
        status, _, reply = self.run_command("process.cancel_job", {"job_id": record["id"]}, confirm="stop job")
        self.assertEqual(status, 200, reply)
        for _ in range(100):
            final = store.record(record["id"])
            if final["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(final["status"], "cancelled")
        self.assertIn("[cancelled", store.tail(record["id"]))
        self.assertEqual(self.preview("process.cancel_job", {"job_id": record["id"]})[2]["blockers"],
                         ["This job is already cancelled."])
        self.assertEqual(self.preview("process.cancel_job", {"job_id": "../etc"})[0], 404)

    def test_upgrade_blocked_while_runs_are_active(self) -> None:
        preview = self.preview("process.upgrade_workers")[2]
        self.assertIn("1 run(s) are active", preview["blockers"][0])
        self.assertEqual(preview["confirm_text"], "upgrade workers")


if __name__ == "__main__":
    unittest.main()
