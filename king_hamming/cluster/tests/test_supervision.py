#!/usr/bin/env python3
"""Test quiet solver control, pipe draining, and supervision metadata."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent
import leader


# Check supervisor behavior that short normal solvers cannot reliably exercise.
class SupervisionTests(unittest.TestCase):
    """Exercise intentional stopping independently of solver stdout."""

    def test_scheduler_retries_database_lock(self) -> None:
        """A transient writer lock must not permanently stop queue advancement."""

        with tempfile.TemporaryDirectory(prefix="kh-scheduler-lock-test-") as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800, lease_seconds=0.15)
            health = leader.SchedulerHealth(0.1)
            stop = threading.Event()
            blocker = sqlite3.connect(database)
            blocker.execute("BEGIN EXCLUSIVE")
            thread = threading.Thread(
                target=leader.scheduler_loop,
                args=(database, 0.15, stop, health, 0.01),
            )

            with patch.object(leader.traceback, "print_exc"):
                thread.start()
                deadline = time.monotonic() + 2
                while health.snapshot()["consecutive_failures"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertGreater(health.snapshot()["consecutive_failures"], 0)
                self.assertTrue(thread.is_alive())
                blocker.rollback()
                blocker.close()
                deadline = time.monotonic() + 2
                while not health.snapshot()["healthy"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                stop.set()
                thread.join(timeout=2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(health.snapshot()["status"], "healthy")

    # Drain a noisy stderr pipe while the solver remains completely quiet on stdout.
    def test_quiet_solver_stop_and_stderr_drain(self) -> None:
        """Stop a quiet child promptly without relying on its progress stream."""

        with tempfile.TemporaryDirectory(prefix="kh-quiet-test-") as directory:
            root = Path(directory)
            ready = root / "ready"
            checkpoint = root / "checkpoint"
            script = root / "quiet.py"
            script.write_text(
                "import os, signal, time\n"
                "from pathlib import Path\n"
                f"checkpoint = Path({str(checkpoint)!r})\n"
                "def stop(signum, frame):\n"
                "    checkpoint.write_text('safe boundary\\n')\n"
                "    raise SystemExit(75)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "os.write(2, b'x' * 262144)\n"
                f"Path({str(ready)!r}).write_text('ready')\n"
                "while True:\n"
                "    time.sleep(0.05)\n"
            )
            requests: list[str] = []

            # The fake leader issues stop only after the entire stderr write succeeds.
            def control(base: str, route: str, value: dict[str, object]) -> dict[str, object]:
                """Return campaign control without generating synthetic progress."""

                requests.append(route)
                self.assertEqual(route, "/v1/run-control")
                return {"stop_requested": ready.exists()}

            command = [
                sys.executable, str(ROOT / "affinity_exec.py"),
                "--cpus", str(min(os.sched_getaffinity(0))), "--",
                sys.executable, str(script),
            ]

            with patch.object(agent, "request_json", side_effect=control):
                result = agent.supervise_solver(
                    command, "unused", {"run_id": "quiet", "lease_token": "token"}, 0.02, 2,
                )

            self.assertTrue(result["stopped"])
            self.assertFalse(result["forced"])
            self.assertEqual(result["return_code"], 75)
            self.assertEqual(len(result["stderr"]), 65536)
            self.assertTrue(checkpoint.exists())
            self.assertGreater(len(requests), 1)
            self.assertEqual(result["progress"]["done"], 0)

    # Snapshot copying must not block campaign control or the independent C heartbeat.
    def test_stop_while_snapshot_is_being_captured(self) -> None:
        """Stop a real C solver during its immutable-boundary pause, then acknowledge safely."""

        with tempfile.TemporaryDirectory(prefix="kh-snapshot-stop-") as directory:
            root = Path(directory)
            entered = threading.Event()
            controls = 0
            observed: list[dict[str, object]] = []

            def request(base: str, route: str, value: dict[str, object]) -> dict[str, object]:
                """Issue stop while the snapshot callback is active and collect real heartbeat phases."""

                nonlocal controls

                if route == "/v1/run-control":
                    controls += 1
                    return {"stop_requested": entered.is_set()}

                self.assertEqual(route, "/v1/progress")
                observed.append(value)
                return {}

            def snapshot(cursor: int, cancelled: threading.Event) -> None:
                """Hold the safe boundary for long enough to exercise independent control polling."""

                self.assertEqual(cursor, 1)
                entered.set()
                time.sleep(0.25)
                self.assertFalse(cancelled.is_set())

            command = [str(ROOT.parent / "dp_solver" / "kh_dp_local"), "5", "3",
                       "--work-dir", str(root / "dp-state"), "-o", str(root / "result.json"),
                       "--tile-side", "7", "--checkpoint-seconds", "0",
                       "--progress-milliseconds", "50", "--checkpoint-handshake"]

            with patch.object(agent, "request_json", side_effect=request):
                result = agent.supervise_solver(command, "unused", {"run_id": "test", "lease_token": "test"},
                                                 0.02, 2, snapshot=snapshot)

            self.assertEqual(result["return_code"], 75)
            self.assertTrue(result["stopped"])
            self.assertGreaterEqual(controls, 3)
            self.assertTrue(any(row.get("phase") == "snapshotting" for row in observed))
            self.assertEqual(result["progress"]["checkpoint_done"], 49)
            self.assertFalse((root / "result.json").exists())

    # Bound a deliberate stop when a solver ignores SIGTERM.
    def test_stop_grace_does_not_trigger_failure_restart(self) -> None:
        """Kill an unresponsive deliberate stop while retaining its earlier checkpoint."""

        with tempfile.TemporaryDirectory(prefix="kh-grace-test-") as directory:
            root = Path(directory)
            ready = root / "ready"
            checkpoint = root / "checkpoint"
            checkpoint.write_text("earlier safe state")
            script = root / "ignore.py"
            script.write_text(
                "import signal, time\nfrom pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"Path({str(ready)!r}).write_text('ready')\n"
                "while True:\n    time.sleep(0.1)\n"
            )

            def control(base: str, route: str, value: dict[str, object]) -> dict[str, object]:
                """Issue an intentional stop after the signal handler is installed."""

                return {"stop_requested": ready.exists()}

            with patch.object(agent, "request_json", side_effect=control):
                result = agent.supervise_solver(
                    [sys.executable, str(script)], "unused",
                    {"run_id": "grace", "lease_token": "token"}, 0.02, 0.05,
                )

            self.assertTrue(result["stopped"] and result["forced"])
            self.assertEqual(result["return_code"], -9)
            self.assertEqual(checkpoint.read_text(), "earlier safe state")

    # Deliberate stop failures remain visible and never enter the automatic retry path.
    def test_failure_while_stopping_is_not_restarted(self) -> None:
        """Record a stop-time checkpoint failure once, rather than restarting it."""

        with tempfile.TemporaryDirectory(prefix="kh-stop-failure-") as directory:
            root = Path(directory)
            result = {
                "return_code": 1, "stopped": True, "forced": False,
                "stderr": "checkpoint write failed", "progress": {},
            }
            job = {
                "run_id": "stop-failure", "lease_token": "token",
                "specification": {"program": "demo", "arguments": {}},
            }

            with patch.object(agent, "supervise_solver", return_value=result) as supervisor:
                with patch.object(agent, "request_json", return_value={}) as request:
                    agent.run_job("unused", job, [min(os.sched_getaffinity(0))], root, root, "unused")

            self.assertEqual(supervisor.call_count, 1)
            self.assertEqual(request.call_args.args[1], "/v1/fail")
            self.assertIn("failed while stopping", request.call_args.args[2]["error"])

    # Existing run records survive idempotent database upgrades.
    def test_schema_migration_retains_runs(self) -> None:
        """Add supervision columns without erasing the pre-supervision database."""

        with tempfile.TemporaryDirectory(prefix="kh-migration-test-") as directory:
            database = Path(directory) / "leader.sqlite"

            with leader.connect(database) as connection:
                connection.executescript(leader.SCHEMA)
                connection.execute(
                    "INSERT INTO runs(run_id, calculation_id, specification, state, priority, "
                    "from_scratch, created) VALUES('old', 'id', '{}', 'complete', 0, 0, 1)"
                )

            leader.initialize(database, 1800)
            leader.initialize(database, 1800)

            with leader.connect(database) as connection:
                row = connection.execute("SELECT * FROM runs WHERE run_id='old'").fetchone()
                self.assertEqual(row["state"], "complete")
                self.assertEqual(row["stop_requested"], 0)
                self.assertIsNone(row["last_solver_heartbeat"])

    # Repeated heartbeats must not reset the mathematical-progress timestamp.
    def test_heartbeat_work_and_checkpoint_are_distinct(self) -> None:
        """Persist status timestamps separately and latch stop across a quick resume."""

        with tempfile.TemporaryDirectory(prefix="kh-status-test-") as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            handler.dispatch_post("/v1/register", {"node_name": "worker"})
            queued = handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}})
            job = handler.dispatch_post("/v1/lease", {"node_name": "worker"})["job"]
            identity = {"run_id": queued["run_id"], "lease_token": job["lease_token"]}

            with patch.object(leader.time, "time", return_value=100.0):
                handler.dispatch_post("/v1/progress", {
                    **identity, "done": 5, "total": 10, "checkpoint_done": 0,
                })

            with patch.object(leader.time, "time", return_value=200.0):
                handler.dispatch_post("/v1/progress", {
                    **identity, "done": 5, "total": 10, "checkpoint_done": 0,
                })

            with leader.connect(database) as connection:
                row = connection.execute("SELECT * FROM runs WHERE run_id=?", (queued["run_id"],)).fetchone()
                self.assertEqual(row["last_solver_heartbeat"], 200.0)
                self.assertEqual(row["last_progress_at"], 100.0)
                self.assertIsNone(row["last_checkpoint_at"])

            # Status warns about stalled work even while solver heartbeats continue.
            captured: list[dict[str, object]] = []
            handler.path = "/v1/status"
            handler.send_json = lambda status, document: captured.append(document)

            for now, health in ((700.0, "no-progress-warning"), (2000.0, "stalled")):
                with patch.object(leader.time, "time", return_value=now):
                    handler.dispatch_post("/v1/progress", {
                        **identity, "done": 5, "total": 10, "checkpoint_done": 0,
                    })
                    handler.do_GET()

                self.assertEqual(captured[-1]["runs"][0]["solver_health"], health)

            handler.dispatch_post("/v1/control", {"state": "stopped"})
            handler.dispatch_post("/v1/control", {"state": "running"})
            control = handler.dispatch_post("/v1/run-control", identity)
            self.assertTrue(control["stop_requested"])
            handler.dispatch_post("/v1/requeue", identity)
            resumed = handler.dispatch_post("/v1/lease", {"node_name": "worker"})["job"]
            self.assertFalse(handler.dispatch_post("/v1/run-control", {
                "run_id": resumed["run_id"], "lease_token": resumed["lease_token"],
            })["stop_requested"])

            # Old leases cannot mutate a run that has been reassigned.
            with self.assertRaises(PermissionError):
                handler.dispatch_post("/v1/progress", {**identity, "done": 6, "total": 10})


# Run with --run explicitly; no arguments show help and a runnable example.
if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Test quiet solver supervision.\nExample: python3 tests/test_supervision.py --run")
    else:
        unittest.main(argv=[sys.argv[0]])
