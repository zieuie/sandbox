#!/usr/bin/env python3
"""Paired physical-host benchmarks against isolated copies of the replicated solver."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from matching_solver.artifacts import load_dp, verify
from matching_solver.polynomials import first_primitive

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def objects(path):
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def baseline(dp_path, poly, hosts, threads, stage, directory, timeout):
    directory.mkdir()
    started = time.monotonic()
    workers = []
    with ExitStack() as stack:
        try:
            descriptors = []
            for index, host in enumerate(hosts):
                command = ["timeout", "--kill-after=5", str(timeout), stage + "/kh_match_worker",
                           "--index", str(index), "--count", str(len(hosts)),
                           "--cpus", ",".join(map(str, range(threads)))]
                stderr = stack.enter_context((directory / f"worker-{index}.log").open("wb"))
                child = subprocess.Popen([*SSH, host, shlex.join(command)], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
                workers.append(child)
                descriptors.extend((child.stdout.fileno(), child.stdin.fileno()))
            command = [str(ROOT / "benchbin/kh_match_distributed"), str(dp_path), str(directory / "result.khmatch"),
                       "--poly", ",".join(map(str, poly)), "--threads-per-worker", str(threads),
                       "--max-edges", "10000000000000", "--max-bytes", str(6*2**30),
                       "--max-field-elements", "100000000", "--checkpoint-seconds", "0"]
            for index in range(len(workers)):
                command.extend(("--worker-fd", f"{descriptors[index*2]},{descriptors[index*2+1]}"))
            stdout = stack.enter_context((directory / "coordinator.jsonl").open("wb"))
            stderr = stack.enter_context((directory / "coordinator.log").open("wb"))
            subprocess.run(command, pass_fds=tuple(descriptors), stdout=stdout, stderr=stderr,
                           check=True, timeout=timeout)
            for worker in workers:
                worker.stdin.close()
                worker.stdout.close()
                worker.wait(timeout=10)
                if worker.returncode:
                    raise RuntimeError("baseline worker failed")
        finally:
            for worker in workers:
                if worker.poll() is None:
                    os.killpg(worker.pid, signal.SIGTERM)
                    worker.wait(timeout=10)
    wall = time.monotonic() - started
    dp, digest = load_dp(dp_path)
    proof = verify(directory / "result.khmatch", dp, digest)
    events = objects(directory / "coordinator.jsonl")
    resources = [r for r in events if r.get("event") == "resource_usage"]
    result = next(r for r in events if "matched" in r and "phases" in r)
    meters = [r for path in directory.glob("*.log") for r in objects(path) if r.get("event") == "wire_meter"]
    report = dict(engine="replicated", solve_wall_seconds=wall, resources=resources, result=result,
                  sent_bytes=sum(r["sent_bytes"] for r in meters), verification=proof,
                  summed_peak_rss_bytes=sum(r["peak_rss_bytes"] for r in resources))
    (directory / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dp", type=Path)
    parser.add_argument("--hosts", default="192.168.4.107,192.168.4.108")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--batch", type=int, default=65536)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0 or not 1 <= args.threads <= 64:
        parser.error("positive repeats/timeout and 1..64 threads are required")
    hosts = args.hosts.split(",")
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    dp, _ = load_dp(args.dp)
    poly = first_primitive(dp["p"], dp["r"], dp["q"])
    stage = "/tmp/kh-multi-baseline-" + secrets.token_hex(8)
    for host in hosts:
        subprocess.run([*SSH, host, shlex.join(["mkdir", "-p", stage])], check=True, timeout=15)
        subprocess.run(["scp", "-q", str(ROOT / "benchbin/kh_match_worker"), f"{host}:{stage}/"], check=True, timeout=30)
    rows = []
    for iteration in range(args.repeats):
        # Alternate ordering to reduce systematic thermal/network timing bias.
        for engine in (("replicated", "partitioned") if iteration % 2 == 0 else ("partitioned", "replicated")):
            directory = args.output_dir / f"{engine}-{iteration}"
            if engine == "replicated":
                metrics = baseline(args.dp.resolve(), poly, hosts, args.threads, stage, directory, args.timeout)
                row = dict(engine=engine, seconds=metrics["solve_wall_seconds"],
                           peak_sum=metrics["summed_peak_rss_bytes"], sent_bytes=metrics["sent_bytes"],
                           phases=metrics["result"]["phases"], scans=metrics["result"]["scans"])
            else:
                command = [sys.executable, str(ROOT / "run.py"), str(args.dp.resolve()), "--poly", ",".join(map(str, poly)),
                           "--hosts", args.hosts, "--threads", str(args.threads), "--batch", str(args.batch),
                           "--timeout", str(args.timeout), "--output-dir", str(directory), "--verify"]
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, timeout=args.timeout+120)
                metrics = json.loads((directory / "metrics.json").read_text())
                row = dict(engine=engine, seconds=metrics["solve_wall_seconds"],
                           peak_sum=sum(r["peak_rss_bytes"] for r in metrics["owners"]),
                           sent_bytes=sum(r["sent_bytes"] for r in metrics["owners"]),
                           phases=metrics["owners"][0]["phases"],
                           scans=sum(r["scanned_edges"] for r in metrics["owners"]))
            row.update(iteration=iteration, threads_per_host=args.threads, p=dp["p"], r=dp["r"], verified=True)
            rows.append(row)
            (args.output_dir / "comparison.json").write_text(json.dumps(rows, indent=2) + "\n")
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
