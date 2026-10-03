#!/usr/bin/env python3
"""End-to-end: match_gpu and GPU DP tiles through an isolated leader with a GPU and a CPU-only agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cluster"))
sys.path.insert(0, str(ROOT / "cluster/tests"))
sys.path.insert(0, str(ROOT))
from test_integration import find_run, free_port, request_json, wait_until  # noqa: E402
from gpu_match_solver.submit import specification  # noqa: E402
from matching_solver.artifacts import load_dp, verify  # noqa: E402
import gpus  # noqa: E402


def main() -> int:
    if "--run" not in sys.argv:
        print(__doc__ + "\nUsage: python3 tests/check_cluster.py --run  (needs a usable CUDA GPU)")
        return 0
    if not gpus.detect():
        print("skip: no usable CUDA device on this host")
        return 0
    with tempfile.TemporaryDirectory(prefix="kh-gpu-cluster-") as name:
        temporary = Path(name)
        base = f"http://127.0.0.1:{free_port()}"
        processes, logs = [], []

        def start(label, argv, env=None):
            log = (temporary / f"{label}.log").open("wb")
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, *argv], stdout=log, stderr=log,
                                              env={**os.environ, **(env or {})}))
        try:
            start("leader", [str(ROOT / "cluster/leader.py"), "serve", "--database", str(temporary / "leader.sqlite"),
                             "--listen", base.removeprefix("http://"), "--checkpoint-seconds", "0"])
            wait_until(lambda: _healthy(base), "leader", timeout=15)
            cpus = ",".join(map(str, sorted(os.sched_getaffinity(0))[:2]))
            lock_dir = temporary / "locks"
            lock_dir.mkdir()
            for label, extra in (("gpu", {}), ("cpu", {"KH_DISABLE_GPU": "1", "KH_DISABLE_GPU_DP": "1"})):
                port = free_port()
                start(label, [str(ROOT / "cluster/agent.py"), "run", "--leader", base, "--name", f"node-{label}",
                              "--cpus", cpus, "--slots", "2",
                              "--work-root", str(temporary / f"work-{label}"),
                              "--storage-root", str(temporary / f"blobs-{label}"),
                              "--storage-listen", f"127.0.0.1:{port}", "--storage-url", f"http://127.0.0.1:{port}",
                              "--poll-seconds", "0.05", "--control-seconds", "0.05"],
                      {"KH_GPU_LOCK_DIR": str(lock_dir), **extra})
            wait_until(lambda: len(request_json(base, "GET", "/v1/status")["nodes"]) == 2, "agents", timeout=30)
            nodes = {node["node_name"]: node for node in request_json(base, "GET", "/v1/status")["nodes"]}
            assert json.loads(nodes["node-gpu"]["gpus_json"]), nodes["node-gpu"]
            assert json.loads(nodes["node-cpu"]["gpus_json"]) == [], nodes["node-cpu"]

            dp_path = ROOT / "matching_solver/examples/13_5.khdp"
            queued = request_json(base, "POST", "/v1/enqueue", {"specification": specification(dp_path, "2,4,0,0,0,1", 2)})
            root = request_json(base, "POST", "/v1/enqueue", {"specification": {
                "program": "dp_distributed", "arguments": {
                    "p": 5, "r": 3, "tile_side": 8, "threads": 1, "max_cpus": 1,
                    "max_visits": 10**9, "max_tile_bytes": 2 * 1024**3}}})

            def finished(run_id):
                row = find_run(base, run_id)
                if row["state"] == "failed":
                    raise AssertionError(row["error"])
                return row if row["state"] == "complete" else None

            row = wait_until(lambda: finished(queued["run_id"]), "GPU matching", timeout=180)
            assert row["node_name"] == "node-gpu" and row["gpu_index"] is not None, row
            usage = row["resource_usage"]
            assert usage and usage[0]["component"] == "solver", usage
            dp, digest = load_dp(dp_path)
            output = temporary / "result.khmatch"
            with urlopen(row["artifact_location"], timeout=10) as response:
                output.write_bytes(response.read())
            summary = verify(output, dp, digest)
            assert summary["status"] == "full_matching" and summary["polynomial"] == [2, 4, 0, 0, 0, 1], summary
            print(f"ok match_gpu on node-gpu device {row['gpu_index']}: {summary['matched']} requests verified")

            dp_row = wait_until(lambda: finished(root["run_id"]), "DP root with GPU tiles", timeout=600)
            reference = temporary / "reference.json"
            subprocess.run([str(ROOT / "dp_solver/kh_dp_local"), "5", "3", "--raw-transitions",
                            "--work-dir", str(temporary / "local-state"), "-o", str(reference)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with urlopen(dp_row["artifact_location"], timeout=10) as response:
                document = json.loads(response.read())
            assert document == json.loads(reference.read_text()), "distributed DP with GPU tiles differs from kh_dp_local"
            gpu_tiles = sum('"engine":"gpu"' in (line or "") for line in _tile_messages(temporary / "leader.sqlite"))
            assert gpu_tiles > 0, "no tile reported GPU computation"
            print(f"ok DP 5^3 root identical to kh_dp_local; {gpu_tiles} tiles computed on the GPU")
            return 0
        except BaseException:
            for log in logs:
                log.flush()
                print(f"--- {Path(log.name).name}\n{Path(log.name).read_text()[-4000:]}", file=sys.stderr)
            raise
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            for log in logs:
                log.close()


def _healthy(base: str) -> bool:
    try:
        return bool(request_json(base, "GET", "/v1/health").get("ok"))
    except OSError:
        return False


def _tile_messages(database: Path) -> list[str]:
    import sqlite3
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return [row[0] for row in connection.execute(
            "SELECT progress_details FROM runs WHERE specification LIKE '%dp_tile%'")]


if __name__ == "__main__":
    sys.exit(main())
