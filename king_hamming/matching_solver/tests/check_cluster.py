#!/usr/bin/env python3
"""Run a pinned matching job through an isolated leader and three worker stores."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
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
    """Test enqueue, checkpoint replication, and final independently verified KHM1."""
    with tempfile.TemporaryDirectory(prefix="kh-match-cluster-") as name:
        temporary = Path(name)
        base = f"http://127.0.0.1:{free_port()}"
        processes = []
        logs = []
        def start(label, argv):
            """Launch one private service and retain its log for failure reporting."""
            log = (temporary / f"{label}.log").open("wb")
            logs.append(log)
            process = subprocess.Popen([sys.executable, *argv], stdout=log, stderr=log)
            processes.append(process)
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
            for index in range(3):
                port = free_port()
                start(f"worker-{index}", [str(ROOT / "cluster/agent.py"), "run", "--leader", base,
                    "--name", f"match-test-{index}", "--cpus", cpu_string,
                    "--work-root", str(temporary / f"work-{index}"),
                    "--storage-root", str(temporary / f"blobs-{index}"),
                    "--storage-listen", f"127.0.0.1:{port}",
                    "--storage-url", f"http://127.0.0.1:{port}",
                    "--poll-seconds", "0.05", "--control-seconds", "0.05"])
            wait_until(lambda: len(request_json(base, "GET", "/v1/status")["nodes"]) == 3,
                       "matching workers", timeout=15)
            dp_path = ROOT / "matching_solver/examples/13_5.khdp"
            job = specification(dp_path, "2,4,0,0,0,1", 2, 2**31)
            queued = request_json(base, "POST", "/v1/enqueue", {"specification": job})
            def complete():
                """Return complete run or fail early with its actual error."""
                row = find_run(base, queued["run_id"])
                if row["state"] == "failed":
                    raise AssertionError(row["error"])
                return row if row["state"] == "complete" else None
            row = wait_until(complete, "matching completion", timeout=90)
            assert row["retained_checkpoints"] >= 1, row
            assert row["checkpoint_replicas"] >= 2, row
            usage = row["resource_usage"]
            assert len(usage) == 1 and usage[0]["component"] == "solver", usage
            assert usage[0]["cpu_microseconds"] > 0 and usage[0]["peak_rss_bytes"] > 0, usage
            with urlopen(row["artifact_location"], timeout=10) as response:
                artifact = response.read()
            assert hashlib.sha256(artifact).hexdigest() == row["artifact_hash"]
            output = temporary / "downloaded.khmatch"
            output.write_bytes(artifact)
            dp, digest = load_dp(dp_path)
            summary = verify(output, dp, digest)
            assert summary["status"] == "full_matching", summary
            # Restore a replicated native phase into a fresh worker directory and resume it.
            with sqlite3.connect(temporary / "leader.sqlite") as connection:
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
            subprocess.run([sys.executable, str(ROOT / "matching_solver/cluster_solver.py"),
                            "--dp", str(copied_input), "--output", str(resumed_output),
                            "--checkpoint", str(restored / "solver.checkpoint.json"),
                            "--poly", "2,4,0,0,0,1", "--threads", "1",
                            "--max-bytes", str(2**31), "--checkpoint-seconds", "1800", "--resume"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
            assert verify(resumed_output, dp, digest)["status"] == "full_matching"
            print(json.dumps({"run_id": row["run_id"], "checkpoints": row["retained_checkpoints"],
                              "checkpoint_replicas": row["checkpoint_replicas"], "artifact": summary["format"],
                              "resource_usage": usage}))
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
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Test isolated matching cluster.\nExample: python3 tests/check_cluster.py --run")
    else:
        raise SystemExit(main())
