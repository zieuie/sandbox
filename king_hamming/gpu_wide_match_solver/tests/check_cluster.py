#!/usr/bin/env python3
"""End-to-end: match_gpu_wide through an isolated leader and one GPU agent, in several row passes,
published in place and verified independently."""

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
from gpu_wide_match_solver.adapter import all_rows_bytes  # noqa: E402
from gpu_wide_match_solver.submit import specification  # noqa: E402
from matching_solver.artifacts import load_dp, verify  # noqa: E402
import gpus  # noqa: E402

MIB = 1024**2


def dp_requests(path: Path) -> int:
    from matching_solver.artifacts import request_count
    return request_count(load_dp(path)[0])


def main() -> int:
    if "--run" not in sys.argv:
        print(__doc__ + "\nUsage: python3 tests/check_cluster.py --run  (needs a usable CUDA GPU)")
        return 0
    if not gpus.detect():
        print("skip: no usable CUDA device on this host")
        return 0
    with tempfile.TemporaryDirectory(prefix="kh-gpu-wide-cluster-") as name:
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
            lock_dir = temporary / "locks"
            lock_dir.mkdir()
            port = free_port()
            start("gpu", [str(ROOT / "cluster/agent.py"), "run", "--leader", base, "--name", "node-gpu",
                          "--cpus", ",".join(map(str, sorted(os.sched_getaffinity(0))[:2])), "--slots", "2",
                          "--work-root", str(temporary / "work"), "--storage-root", str(temporary / "blobs"),
                          "--storage-listen", f"127.0.0.1:{port}", "--storage-url", f"http://127.0.0.1:{port}",
                          "--poll-seconds", "0.05", "--control-seconds", "0.05"],
                  {"KH_GPU_LOCK_DIR": str(lock_dir)})
            wait_until(lambda: len(request_json(base, "GET", "/v1/status")["nodes"]) == 1, "agent", timeout=30)
            node = request_json(base, "GET", "/v1/status")["nodes"][0]
            assert json.loads(node["gpus_json"]), node

            # 20 MiB blocks force 13^5 into several blocks on a GPU that could hold it whole, and rows
            # for two thirds of its cells force several passes (the last block holds the most cells).
            dp_path = ROOT / "matching_solver/examples/13_5.khdp"
            rows = all_rows_bytes(load_dp(dp_path)[0], 2) * 2 // 3
            queued = request_json(base, "POST", "/v1/enqueue", {"specification": specification(
                dp_path, "2,4,0,0,0,1", 2, gpu_memory_bytes=512 * MIB, block_device_bytes=20 * MIB,
                row_bytes=rows)})

            def finished():
                row = find_run(base, queued["run_id"])
                if row["state"] == "failed":
                    raise AssertionError(row["error"])
                return row if row["state"] == "complete" else None

            row = wait_until(finished, "GPU wide matching", timeout=240)
            assert row["gpu_index"] is not None, row
            summary = json.loads(row["progress_message"])
            assert summary["engine"] == "gpu-wide" and summary["blocks"] >= 2 and summary["passes"] >= 2, summary
            assert summary["trace"][0][1] == dp_requests(dp_path) and summary["trace"][-1][1] == 0, summary["trace"]
            # Every stage the bridge saw, in order and finished (the agent's verification follows).
            stages = [item["key"] for item in summary["stages"]]
            assert stages[:3] == ["gpu_wait", "field", "blocks"] and stages[-2:] == ["check", "publish"], stages
            assert all(item["finished"] and item["finished"] >= item["started"] for item in summary["stages"]), summary
            blocks = next(item for item in summary["stages"] if item["key"] == "blocks")
            assert blocks["done"] == blocks["total"] == summary["blocks"], blocks
            dp, digest = load_dp(dp_path)
            output = temporary / "result.khmatch"
            with urlopen(row["artifact_location"], timeout=10) as response:
                output.write_bytes(response.read())
            verified = verify(output, dp, digest)
            assert verified["status"] == "full_matching" and verified["polynomial"] == [2, 4, 0, 0, 0, 1], verified
            print(f"ok match_gpu_wide on device {row['gpu_index']}: {verified['matched']} requests in "
                  f"{summary['blocks']} blocks and {summary['passes']} passes, {summary['rounds']} round(s), "
                  "published in place, verified")
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


if __name__ == "__main__":
    sys.exit(main())
