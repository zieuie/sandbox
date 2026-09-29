#!/usr/bin/env python3
"""Exercise the first draft through its public processes and HTTP protocol."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


# Reserve a likely-free local port for the short test lifetime.
def free_port() -> int:
    """Return a currently unused loopback TCP port."""

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


# Exchange one JSON object with the leader.
def request_json(base: str, method: str, path: str, value: Any = None) -> Any:
    """Send an HTTP request and return its decoded JSON response."""

    body = None if value is None else json.dumps(value).encode("utf-8")
    request = Request(f"{base}{path}", data=body, method=method)

    if body is not None:
        request.add_header("Content-Type", "application/json")

    with urlopen(request, timeout=5) as response:
        return json.load(response)


# Wait for a predicate while retaining a useful timeout error.
def wait_until(predicate: Any, description: str, timeout: float = 10.0) -> Any:
    """Return the first truthy predicate result before timeout."""

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        result = predicate()

        if result:
            return result

        time.sleep(0.05)

    raise AssertionError(f"timed out waiting for {description}")


# Return one run record from leader status.
def find_run(base: str, run_id: str) -> dict[str, Any]:
    """Fetch and return run_id from current leader status."""

    status = request_json(base, "GET", "/v1/status")

    for run in status["runs"]:
        if run["run_id"] == run_id:
            return run

    raise AssertionError(f"run {run_id} missing from status")


# Run the complete queue, recovery, stop, rerun, and artifact scenario.
def main() -> int:
    """Execute the integration test and return an exit status."""

    os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"
    leader_port = free_port()
    storage_ports = [free_port() for _ in range(3)]
    leader_url = f"http://127.0.0.1:{leader_port}"
    available_cpus = sorted(os.sched_getaffinity(0))[:2]
    cpu_list = ",".join(str(cpu) for cpu in available_cpus)

    with tempfile.TemporaryDirectory(prefix="kh-first-test-") as temporary_name:
        temporary = Path(temporary_name)
        leader = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "leader.py"),
                "serve",
                "--database",
                str(temporary / "leader.sqlite"),
                "--listen",
                f"127.0.0.1:{leader_port}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        agents: list[subprocess.Popen[str]] = []

        try:
            # Wait until the leader socket accepts real protocol requests.
            def leader_ready() -> bool:
                """Return whether the leader health endpoint responds."""

                try:
                    return bool(request_json(leader_url, "GET", "/v1/health")["ok"])
                except (OSError, URLError):
                    return False

            wait_until(leader_ready, "leader startup")
            # Three agents allow the final-artifact replication target to be tested.
            for index, storage_port in enumerate(storage_ports):
                agent = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "agent.py"),
                        "run",
                        "--leader",
                        leader_url,
                        "--name",
                        f"integration-worker-{index}",
                        "--cpus",
                        cpu_list,
                        "--work-root",
                        str(temporary / f"work-{index}"),
                        "--storage-root",
                        str(temporary / f"blobs-{index}"),
                        "--storage-listen",
                        f"127.0.0.1:{storage_port}",
                        "--storage-url",
                        f"http://127.0.0.1:{storage_port}",
                        "--poll-seconds",
                        "0.02",
                    "--control-seconds",
                    "0.05",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                agents.append(agent)

            wait_until(
                lambda: len(request_json(leader_url, "GET", "/v1/status")["nodes"]) == 3,
                "agent registration",
            )

            # Force one child failure and require checkpoint recovery to completion.
            specification = {
                "program": "demo",
                "arguments": {"steps": 8, "delay": 0.02, "fail_once_at": 3},
            }
            first = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": specification},
            )

            def completed_demo():
                """Return the completed fixture run and surface terminal failures immediately."""

                run = find_run(leader_url, first["run_id"])
                if run["state"] == "failed":
                    raise AssertionError(run["error"])
                return run if run["state"] == "complete" else None

            first_run = wait_until(
                completed_demo,
                "checkpoint-restarted run",
            )
            assert first_run["progress_done"] == 8

            # Verify that the advertised content hash matches the served artifact.
            with urlopen(first_run["artifact_location"], timeout=5) as response:
                artifact = response.read()

            assert hashlib.sha256(artifact).hexdigest() == first_run["artifact_hash"]
            artifact_path = temporary / "downloaded-result.bin"
            specification_path = temporary / "artifact-specification.json"
            artifact_path.write_bytes(artifact)
            specification_path.write_text(json.dumps(specification, sort_keys=True) + "\n")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "verify_artifact.py"),
                    str(specification_path),
                    str(artifact_path),
                    "--sha256",
                    first_run["artifact_hash"],
                ],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )

            # All three workers eventually hold and advertise the final artifact.
            def fully_replicated() -> bool:
                """Return whether the first artifact reached its replica target."""

                status = request_json(leader_url, "GET", "/v1/status")

                for candidate in status["artifacts"]:
                    if candidate["artifact_hash"] == first_run["artifact_hash"]:
                        return candidate["replicas"] == 3

                return False

            wait_until(fully_replicated, "three final-artifact replicas")

            # Route a real C DP job through the same lease and artifact boundary.
            dp_specification = {
                "program": "dp",
                "arguments": {
                    "p": 3,
                    "r": 3,
                    "tile_side": 4,
                    "threads": len(available_cpus),
                    "progress_milliseconds": 100,
                    "max_state_bytes": 1_000_000,
                    "max_visits": 1_000_000,
                },
            }
            dp = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": dp_specification},
            )
            dp_run = wait_until(
                lambda: (run if (run := find_run(leader_url, dp["run_id"]))["state"] == "complete" else None),
                "production C DP run",
            )

            with urlopen(dp_run["artifact_location"], timeout=5) as response:
                dp_artifact = response.read()

            dp_path = temporary / "distributed-dp-result.json"
            dp_path.write_bytes(dp_artifact)
            subprocess.run(
                [sys.executable, str(ROOT.parent / "dp_solver" / "verify_dp.py"), str(dp_path)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )

            # Ordinary duplicates reuse; reruns retain a distinct attempt.
            duplicate = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": specification},
            )
            assert duplicate["reused"] is True
            assert duplicate["run_id"] == first["run_id"]
            rerun = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": specification, "rerun": True},
            )
            assert rerun["run_id"] != first["run_id"]
            wait_until(
                lambda: find_run(leader_url, rerun["run_id"])["state"] == "complete",
                "retained rerun",
            )
            from_scratch = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": specification, "from_scratch": True},
            )
            assert from_scratch["run_id"] not in {first["run_id"], rerun["run_id"]}
            wait_until(
                lambda: find_run(leader_url, from_scratch["run_id"])["state"] == "complete",
                "retained from-scratch run",
            )

            # Stop an active calculation at a checkpoint, then resume it.
            slow_specification = {
                "program": "demo",
                "arguments": {"steps": 30, "delay": 0.03},
            }
            slow = request_json(
                leader_url,
                "POST",
                "/v1/enqueue",
                {"specification": slow_specification},
            )
            wait_until(
                lambda: find_run(leader_url, slow["run_id"])["progress_done"] >= 2,
                "active slow run",
            )
            request_json(leader_url, "POST", "/v1/control", {"state": "stopped"})
            paused = wait_until(
                lambda: (run if (run := find_run(leader_url, slow["run_id"]))["state"] == "queued" else None),
                "checkpointed global stop",
            )
            assert 1 < paused["progress_done"] < 30
            request_json(leader_url, "POST", "/v1/control", {"state": "running"})
            wait_until(
                lambda: find_run(leader_url, slow["run_id"])["state"] == "complete",
                "resumed run",
            )
            # Observe work inside a real long tile before any durable checkpoint.
            long_specification = {
                "program": "dp",
                "arguments": {
                    "p": 11, "r": 5, "threads": 1, "tile_side": 4096,
                    "progress_milliseconds": 50,
                    "max_state_bytes": 100_000_000, "max_visits": 3_000_000_000,
                },
            }
            long_job = request_json(
                leader_url, "POST", "/v1/enqueue", {"specification": long_specification},
            )

            def inside_tile() -> dict[str, Any] | None:
                """Return a genuine live record before its first checkpoint."""

                row = find_run(leader_url, long_job["run_id"])
                return row if 0 < row["progress_done"] < row["progress_total"] else None

            live = wait_until(inside_tile, "live progress inside a long DP tile")
            assert live["progress_checkpoint_done"] == 0
            assert live["last_checkpoint_at"] is None
            assert live["last_solver_heartbeat"] is not None
            assert live["progress_units"] == "cells"
            request_json(leader_url, "POST", "/v1/control", {"state": "stopped"})
            stopped = wait_until(
                lambda: (row if (row := find_run(leader_url, long_job["run_id"]))["state"] == "queued" else None),
                "signal-boundary checkpoint through agent supervision", timeout=30,
            )
            assert stopped["progress_done"] == stopped["progress_checkpoint_done"]
            assert stopped["error"] is None
            assert stopped["progress_phase"] == "stopped"
            request_json(leader_url, "POST", "/v1/control", {"state": "running"})
            completed = wait_until(
                lambda: (row if (row := find_run(leader_url, long_job["run_id"]))["state"] == "complete" else None),
                "resumed long DP job", timeout=30,
            )

            with urlopen(completed["artifact_location"], timeout=5) as response:
                assert json.load(response)["theta"] == 4181

        finally:
            for agent in agents:
                agent.terminate()

            for agent in agents:
                try:
                    agent.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    agent.kill()
                    agent.wait(timeout=3)

            leader.terminate()

            try:
                leader.wait(timeout=3)
            except subprocess.TimeoutExpired:
                leader.kill()
                leader.wait(timeout=3)

            # Surface child logs when a child died unexpectedly.
            for agent in agents:
                if agent.returncode not in {0, -15}:
                    raise AssertionError(agent.stderr.read())

            if leader.returncode not in {0, -15}:
                raise AssertionError(leader.stderr.read())

    print("first-draft integration test passed")
    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
