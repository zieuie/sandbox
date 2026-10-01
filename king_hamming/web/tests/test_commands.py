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

    def test_upgrade_blocked_while_runs_are_active(self) -> None:
        preview = self.preview("process.upgrade_workers")[2]
        self.assertIn("1 run(s) are active", preview["blockers"][0])
        self.assertEqual(preview["confirm_text"], "upgrade workers")


if __name__ == "__main__":
    unittest.main()
