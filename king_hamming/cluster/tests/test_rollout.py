#!/usr/bin/env python3
"""Check guarded in-place upgrades of retained campaign workers."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dp_solver import launch_dp


def retained_state(directory: str, states: list[tuple[str, str]]) -> Path:
    """Create the minimum retained state consumed by the rollout guard."""
    state = Path(directory)
    manifest = {
        "leader": "http://private",
        "workers": [
            {"host": "192.0.2.1", "root": "/srv/king_hamming/campaign",
             "pid": 10, "start": "100"},
            {"host": "192.0.2.2", "root": "/srv/king_hamming/campaign",
             "pid": 20, "start": "200"},
        ],
    }
    (state / "manifest.json").write_text(json.dumps(manifest))
    with sqlite3.connect(state / "leader.sqlite") as database:
        database.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        database.execute("INSERT INTO settings VALUES ('campaign_state','running')")
        database.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        database.executemany("INSERT INTO runs VALUES (?,?)", states)
    return state / "manifest.json"


class RolloutTests(unittest.TestCase):
    """Upgrades never interrupt work and retain worker data roots."""

    def test_drain_stops_new_dispatch_without_stopping_active_tile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            retained_state(directory, [("tile-child", "running")])
            self.assertEqual(launch_dp.drain_dispatch(Path(directory) / "leader.sqlite"), 1)
            with sqlite3.connect(Path(directory) / "leader.sqlite") as database:
                self.assertEqual(database.execute(
                    "SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0],
                    "stopped")
                self.assertEqual(database.execute(
                    "SELECT state FROM runs WHERE run_id='tile-child'").fetchone()[0],
                    "running")

    def test_active_child_refuses_before_control_or_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = retained_state(directory, [("tile-child", "running")])
            with patch.object(launch_dp, "request") as request, \
                    patch.object(launch_dp, "stop_owned_worker") as stop:
                with self.assertRaisesRegex(ValueError, "tile-child"):
                    launch_dp.upgrade_workers(manifest)
            request.assert_not_called()
            stop.assert_not_called()
            with sqlite3.connect(Path(directory) / "leader.sqlite") as database:
                self.assertEqual(database.execute(
                    "SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0],
                    "running")

    def test_idle_upgrade_replaces_all_workers_and_stays_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = retained_state(directory, [("finished", "complete")])
            launched = [
                {"host": "192.0.2.1", "root": "/srv/king_hamming/campaign",
                 "pid": 11, "start": "101"},
                {"host": "192.0.2.2", "root": "/srv/king_hamming/campaign",
                 "pid": 21, "start": "201"},
            ]

            def fake_request(_leader, route, value=None):
                self.assertEqual(route, "/v1/status")
                version = hashlib.sha256(b"bundle").hexdigest()
                return {"nodes": [
                    {"node_name": "dp-1", "state": "healthy",
                     "runtime_version": version},
                    {"node_name": "dp-2", "state": "healthy",
                     "runtime_version": version},
                ]}

            with patch.object(launch_dp, "request", side_effect=fake_request) as request, \
                    patch.object(launch_dp.subprocess, "run") as build, \
                    patch.object(launch_dp, "bundle", return_value=b"bundle"), \
                    patch.object(launch_dp, "stop_owned_worker") as stop, \
                    patch.object(launch_dp, "validate_owned_leader"), \
                    patch.object(launch_dp, "validate_owned_feeder"), \
                    patch.object(launch_dp, "restart_owned_leader") as restart, \
                    patch.object(launch_dp, "restart_owned_feeder",
                                 return_value=True) as restart_feeder, \
                    patch.object(launch_dp, "replace_runtime") as replace, \
                    patch.object(launch_dp, "launch_worker", side_effect=launched):
                launch_dp.upgrade_workers(manifest)

            self.assertEqual(stop.call_count, 2)
            restart.assert_called_once()
            restart_feeder.assert_called_once_with(Path(directory))
            self.assertEqual(replace.call_args_list, [
                call("192.0.2.1", "/srv/king_hamming/campaign", b"bundle"),
                call("192.0.2.2", "/srv/king_hamming/campaign", b"bundle"),
            ])
            built = [Path(item.args[0][2]).name for item in build.call_args_list]
            self.assertEqual(built, ["dp_solver", "matching_solver", "matching_solver_multi",
                                     "cuda", "gpu_match_solver", "gpu_block_match_solver", "gpu_wide_match_solver", "gpu_dp_solver"])
            self.assertTrue(request.call_args_list)
            self.assertTrue(all(item.args[1] == "/v1/status"
                                for item in request.call_args_list))
            saved = json.loads(manifest.read_text())
            self.assertEqual(saved["workers"], launched)
            self.assertEqual(saved["state"], "stopped-after-upgrade")
            with sqlite3.connect(Path(directory) / "leader.sqlite") as database:
                self.assertEqual(database.execute(
                    "SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0],
                    "stopped")


if __name__ == "__main__":
    if "--run" not in sys.argv:
        print("Test retained rollout guards. Example: python3 test_rollout.py --run")
    else:
        sys.argv.remove("--run")
        unittest.main()
