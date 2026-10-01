#!/usr/bin/env python3
"""Exercise real leader/agent leases, durable pause/restore, and stale-peer fencing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
sys.path.insert(0, str(ROOT / "cluster/tests"))
from test_integration import find_run, free_port, request_json, wait_until
from matching_solver_multi.submit import specification
from matching_solver.artifacts import load_dp, verify


class ClusterTests(unittest.TestCase):
    def test_fenced_group_pause_restore_and_verified_result(self):
        with tempfile.TemporaryDirectory(prefix="kh-owned-cluster-") as folder:
            root = Path(folder)
            base = f"http://127.0.0.1:{free_port()}"
            children, logs = [], []

            def start(name, command):
                log = (root / f"{name}.log").open("wb")
                logs.append(log)
                child = subprocess.Popen([sys.executable, *command], stdout=log, stderr=log)
                children.append(child)
                return child

            try:
                start("leader", [str(ROOT / "cluster/leader.py"), "serve", "--database", str(root / "leader.sqlite"),
                                 "--listen", base[7:], "--checkpoint-seconds", "0"])

                def ready():
                    try:
                        return request_json(base, "GET", "/v1/health").get("ok")
                    except OSError:
                        return False

                wait_until(ready, "owned matching leader", timeout=15)
                cpus = sorted(os.sched_getaffinity(0))
                if len(cpus) < 2:
                    self.skipTest("needs two isolated logical CPUs")
                agents, agent_commands = [], []
                for rank in range(3):
                    port = free_port()
                    command = [str(ROOT / "cluster/agent.py"), "run", "--leader", base,
                         "--name", f"owned-{rank}", "--cpus", str(cpus[rank % len(cpus)]),
                         "--work-root", str(root / f"work-{rank}"), "--storage-root", str(root / f"blobs-{rank}"),
                         "--storage-listen", f"127.0.0.1:{port}", "--storage-url", f"http://127.0.0.1:{port}",
                         "--poll-seconds", "0.05", "--control-seconds", "0.05"]
                    agent_commands.append(command)
                    agents.append(start(f"agent-{rank}", command))
                wait_until(lambda: len(request_json(base, "GET", "/v1/status")["nodes"]) == 3,
                           "owned matching agents", timeout=15)
                source = ROOT / "examples/7_5.khdp"
                job = specification(source, "4,1,0,0,0,1", workers=3, batch=4096)
                job["arguments"]["checkpoint_phases"] = 1
                run_id = request_json(base, "POST", "/v1/enqueue", {"specification": job})["run_id"]

                def first_checkpoint():
                    row = find_run(base, run_id)
                    if row["state"] == "failed":
                        raise AssertionError(row["error"])
                    return row if row["retained_checkpoints"] else None

                before = wait_until(first_checkpoint, "durable owned checkpoint", timeout=45)
                stale = before["lease_token"]
                request_json(base, "POST", "/v1/run-command", {"run_id": run_id, "action": "pause"})
                paused = wait_until(lambda: (row if (row := find_run(base, run_id))["state"] == "paused" else None),
                                    "owned group pause", timeout=45)
                self.assertGreater(paused["progress_checkpoint_done"], 0)
                self.assertEqual(paused["engine_failures"], 0)
                # A lease is not valid merely because the participant still lives.
                node = request_json(base, "GET", "/v1/status")["nodes"][1]
                with self.assertRaises(HTTPError) as rejected:
                    request_json(base, "POST", "/v1/peer-authorize", {
                        "node_name": node["node_name"], "session_id": node["session_id"],
                        "run_id": run_id, "lease_token": stale, "worker_index": 1, "worker_count": 3})
                self.assertEqual(rejected.exception.code, 409)
                request_json(base, "POST", "/v1/run-command", {"run_id": run_id, "action": "resume"})

                def complete():
                    row = find_run(base, run_id)
                    if row["state"] == "failed":
                        raise AssertionError(row["error"])
                    return row if row["state"] == "complete" else None

                final = wait_until(complete, "owned matching completion", timeout=90)
                self.assertGreater(final["restored_done"], 0)
                self.assertEqual(final["engine_failures"], 0)
                with urlopen(final["artifact_location"], timeout=10) as response:
                    raw = response.read()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), final["artifact_hash"])
                output = root / "verified.khmatch"
                output.write_bytes(raw)
                dp, digest = load_dp(source)
                self.assertEqual(verify(output, dp, digest)["status"], "full_matching")
                # Force an actual participant session loss after a durable
                # phase, then let the generic runtime fence and recover it.
                run_id = request_json(base, "POST", "/v1/enqueue", {"specification": job, "rerun": True})["run_id"]
                wait_until(first_checkpoint, "phase before forced peer loss", timeout=45)
                status = request_json(base, "GET", "/v1/status")
                partner = next(node for node in status["nodes"] if node.get("reserved_for") == run_id)
                index = int(partner["node_name"].split("-")[-1])
                agents[index].terminate()
                agents[index].wait(timeout=5)
                agents[index] = start(f"agent-{index}-restarted", agent_commands[index])
                recovered = wait_until(complete, "forced peer recovery", timeout=90)
                self.assertGreater(recovered["restored_done"], 0)
                self.assertGreaterEqual(recovered["engine_failures"] + recovered["recovery_count"], 1)
                with urlopen(recovered["artifact_location"], timeout=10) as response:
                    output.write_bytes(response.read())
                self.assertEqual(verify(output, dp, digest)["status"], "full_matching")
            except Exception:
                for path in sorted(root.glob("*.log")):
                    print(f"{path.name}:\n{path.read_text(errors='replace')[-5000:]}", file=sys.stderr)
                raise
            finally:
                for child in reversed(children):
                    child.terminate()
                for child in reversed(children):
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                for log in logs:
                    log.close()


if __name__ == "__main__":
    unittest.main()
