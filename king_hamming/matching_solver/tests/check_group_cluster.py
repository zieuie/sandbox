#!/usr/bin/env python3
"""Run and recover a multi-node matching group in an isolated cluster."""

from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
import shutil
import time
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cluster"))
sys.path.insert(0, str(ROOT / "cluster/tests"))
sys.path.insert(0, str(ROOT))
from test_integration import find_run, free_port, request_json, wait_until
from matching_solver.submit import specification
from matching_solver.artifacts import load_dp, verify
from checkpoints import restore_checkpoint
from blob_store import blob_path
from common import calculation_id


# Keep process output in temporary logs so a failed test reports a useful cause.
def main() -> int:
    """Test group fencing, replicated restore, and final verified KHM1."""
    with tempfile.TemporaryDirectory(prefix="kh-match-cluster-",
                                     ignore_cleanup_errors=True) as name:
        temporary = Path(name)
        base = f"http://127.0.0.1:{free_port()}"
        processes = []
        logs = []
        def start(label, argv):
            """Launch one private service and retain its log for failure reporting."""
            log = (temporary / f"{label}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen([sys.executable, *argv], stdout=log, stderr=log,
                                       env={**os.environ, "KH_MATCH_TEST_PHASE_DELAY": "1"})
            processes.append(process)
            return process
        try:
            start("leader", [str(ROOT / "cluster/leader.py"), "serve", "--database", str(temporary / "leader.sqlite"),
                             "--listen", base.removeprefix("http://"), "--checkpoint-seconds", "0"])
            def leader_ready():
                """Return true once the isolated leader accepts HTTP requests."""
                try:
                    return request_json(base, "GET", "/v1/health").get("ok")
                except OSError:
                    return False
            wait_until(leader_ready, "matching test leader", timeout=15)
            cpus = sorted(os.sched_getaffinity(0))[:2]
            cpu_string = ",".join(map(str, cpus))
            agents = []
            agent_commands = []
            for index in range(4):
                port = free_port()
                command = [str(ROOT / "cluster/agent.py"), "run", "--leader", base,
                    "--name", f"match-test-{index}", "--cpus", cpu_string,
                    "--work-root", str(temporary / f"work-{index}"),
                    "--storage-root", str(temporary / f"blobs-{index}"),
                    "--storage-listen", f"127.0.0.1:{port}",
                    "--storage-url", f"http://127.0.0.1:{port}",
                    "--poll-seconds", "0.05", "--control-seconds", "0.05"]
                agent_commands.append(command)
                agents.append(start(f"worker-{index}", command))
            wait_until(lambda: len(request_json(base, "GET", "/v1/status")["nodes"]) == 4,
                       "matching workers", timeout=15)
            dp_path = ROOT / "examples/5_3.khdp"
            job = specification(dp_path, "2,3,0,1", 2, 2**31,
                                distributed=True, workers=4)
            queued = request_json(base, "POST", "/v1/enqueue", {"specification": job})
            def checkpointed():
                status = request_json(base, "GET", "/v1/status")
                run = next(run for run in status["runs"] if run["run_id"] == queued["run_id"])
                partner = next((node for node in status["nodes"]
                                if node["reserved_for"] == queued["run_id"]), None)
                return partner if run["retained_checkpoints"] >= 1 else None
            partner = wait_until(checkpointed, "first replicated group phase", timeout=30)
            stale_token = find_run(base, queued["run_id"])["lease_token"]
            index = int(partner["node_name"].split("-")[-1])
            agents[index].terminate()
            agents[index].wait(timeout=5)
            agents[index] = start(f"worker-{index}-restarted", agent_commands[index])
            def complete():
                """Return complete run or fail early with its actual error."""
                row = find_run(base, queued["run_id"])
                if row["state"] == "failed":
                    raise AssertionError(row["error"])
                return row if row["state"] == "complete" else None
            row = wait_until(complete, "matching completion", timeout=90)
            assert row["recovery_count"] + row["engine_failures"] >= 1, row
            assert row["restored_done"] > 0, row
            final_usage = [item for item in row["resource_usage"]
                           if item["lease_token"] == row["lease_token"]]
            assert len(final_usage) == 5, final_usage
            assert {item["component"] for item in final_usage} == {"coordinator", "shard"}, final_usage
            assert {item["shard_index"] for item in final_usage
                    if item["component"] == "shard"} == {0, 1, 2, 3}, final_usage
            assert all(item["cpu_microseconds"] > 0 and item["peak_rss_bytes"] > 0
                       for item in final_usage), final_usage
            current_partner = next(node for node in request_json(base, "GET", "/v1/status")["nodes"]
                                   if node["node_name"] == partner["node_name"])
            try:
                request_json(base, "POST", "/v1/peer-authorize",
                             {"node_name": partner["node_name"],
                              "session_id": current_partner["session_id"],
                              "run_id": queued["run_id"], "lease_token": stale_token})
            except HTTPError as error:
                assert error.code == 409, error
            else:
                raise AssertionError("completed group token reopened a peer worker")
            assert row["retained_checkpoints"] >= 1, row
            assert row["checkpoint_replicas"] >= 2, row
            with urlopen(row["artifact_location"], timeout=10) as response:
                artifact = response.read()
            assert hashlib.sha256(artifact).hexdigest() == row["artifact_hash"]
            output = temporary / "downloaded.khmatch"
            output.write_bytes(artifact)
            dp, digest = load_dp(dp_path)
            summary = verify(output, dp, digest)
            assert summary["status"] == "full_matching", summary
            # Restore a replicated native phase into a fresh worker directory and resume it.
            with closing(sqlite3.connect(temporary / "leader.sqlite")) as connection:
                record = connection.execute("SELECT manifest FROM checkpoints WHERE run_id=? ORDER BY cursor DESC LIMIT 1",
                                            (row["run_id"],)).fetchone()
            assert record is not None
            manifest = json.loads(record[0])
            identity = calculation_id(manifest)
            storage = next(root for root in temporary.glob("blobs-*") if blob_path(root, identity).exists() and
                           all(blob_path(root, item["sha256"]).exists() for item in manifest["files"]))
            restored = temporary / "restored"
            task = {"manifest": manifest, "manifest_hash": identity, "sources": []}
            restore_checkpoint(task, job, row["run_id"], restored, storage)
            copied_input = restored / "input.khdp"
            copied_input.write_bytes(dp_path.read_bytes())
            resumed_output = restored / "resumed.khmatch"
            subprocess.run([sys.executable, str(ROOT / "matching_solver/native_coordinator.py"),
                            str(copied_input), "--output", str(resumed_output),
                            "--checkpoint-dir", str(restored / "phase-state"),
                            "--poly", "2,3,0,1", "--worker", "local", "--worker", "local",
                            "--resume", str(restored / "solver.checkpoint.json")],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
            assert verify(resumed_output, dp, digest)["status"] == "full_matching"
            # The 7^5 prepare frame exceeds one socket write; verify exact peer framing.
            large_dp_path = ROOT / "examples/7_5.khdp"
            large_job = specification(large_dp_path, "4,1,0,0,0,1", 2, 2**31,
                                      distributed=True)
            large_queued = request_json(base, "POST", "/v1/enqueue",
                                        {"specification": large_job})
            def large_complete():
                """Return the second group result after its large peer frame commits."""
                result = find_run(base, large_queued["run_id"])
                if result["state"] == "failed":
                    raise AssertionError(result["error"])
                return result if result["state"] == "complete" else None
            large_row = wait_until(large_complete, "large-frame matching", timeout=90)
            with urlopen(large_row["artifact_location"], timeout=10) as response:
                large_artifact = response.read()
            large_output = temporary / "large.khmatch"
            large_output.write_bytes(large_artifact)
            large_dp, large_digest = load_dp(large_dp_path)
            assert verify(large_output, large_dp, large_digest)["status"] == "full_matching"
            print(json.dumps({"run_id": row["run_id"], "checkpoints": row["retained_checkpoints"],
                              "checkpoint_replicas": row["checkpoint_replicas"], "artifact": summary["format"],
                              "recoveries": row["recovery_count"] + row["engine_failures"],
                              "large_field": large_dp["q"], "resource_records": len(final_usage)}))
        except Exception:
            for path in sorted(temporary.glob("*.log")):
                print(f"{path.name}:\n{path.read_text(errors='replace')[-4000:]}", file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                process.terminate()
            for process in reversed(processes):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for log in logs:
                log.close()
    # Agent storage threads may finish an in-flight write as shutdown completes.
    for attempt in range(20):
        if not Path(name).exists():
            break
        try:
            shutil.rmtree(name)
        except OSError as error:
            if attempt == 19:
                raise AssertionError(f"private test files remain in {name}: {error}") from error
            time.sleep(0.1)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Test isolated multi-node matching and partner restart.\nExample: python3 tests/check_group_cluster.py --run")
    else:
        raise SystemExit(main())
