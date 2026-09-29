#!/usr/bin/env python3
"""Run an isolated real-host DP checkpoint failover experiment over SSH."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
import concurrent.futures
import hashlib
import io
import ipaddress
import json
import os
import shlex
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent / "cluster"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


from deployment import remote, request, wait, bundle, deploy, start_worker, stop_worker, collect_and_remove

# Hash real resumed state on the replacement without copying the whole matrices back.
def state_hashes(host: str, directory: str, run_id: str, token: str) -> dict[str, Any]:
    """Return full value/choice SHA-256 hashes from the replacement's completed lease."""

    code = """import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])/'work'/sys.argv[2]/sys.argv[3]
result={}
for name in ('values.bin','choices.bin'):
    digest=hashlib.sha256()
    with (root/'dp-state'/name).open('rb') as source:
        while block:=source.read(1048576):
            digest.update(block)
    result[name]=digest.hexdigest()
result['artifact_hash']=hashlib.sha256((root/'result.bin').read_bytes()).hexdigest()
print(json.dumps(result))
"""
    return remote(host, code, [directory, run_id, token])


# Build an explicit interface that performs no SSH or computation without --run.
def build_parser() -> argparse.ArgumentParser:
    """Return the real-host experiment parser with useful no-argument help."""

    parser = argparse.ArgumentParser(
        description="Test real-host DP checkpoint failover in isolated temporary deployments.",
        epilog="Example: ./cluster_smoke.py --run --hosts 192.168.4.101 192.168.4.102 192.168.4.103",
    )
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--hosts", nargs=3, default=["192.168.4.101", "192.168.4.102", "192.168.4.103"])
    parser.add_argument("--leader-address", default="192.168.4.151")
    parser.add_argument("--prime", type=int, default=13)
    parser.add_argument("--degree", type=int, default=5)
    parser.add_argument("--output", type=Path, default=ROOT / "experiments")
    return parser


# Validate an actual worker disappearance, cross-host restore, and complete-table equality.
def main() -> int:
    """Run the requested experiment, save evidence, and clean up all owned processes."""

    parser = build_parser()
    arguments = parser.parse_args()

    if not arguments.run:
        parser.print_help()
        return 0

    for host in [*arguments.hosts, arguments.leader_address]:
        ipaddress.ip_address(host)

    if len(set(arguments.hosts)) != 3:
        parser.error("three distinct worker addresses are required")

    experiment = "kh-recovery-" + uuid.uuid4().hex
    directory = "/tmp/" + experiment
    evidence = arguments.output / experiment
    evidence.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"experiment": experiment, "hosts": arguments.hosts, "cleanup": {}}
    archive = bundle()
    deployed: list[str] = []
    workers: dict[str, dict[str, Any]] = {}
    names = {host: f"smoke-{host.rsplit('.',1)[1]}" for host in arguments.hosts}
    started = time.monotonic()
    leader_process: subprocess.Popen[bytes] | None = None
    local_directory = tempfile.TemporaryDirectory(prefix="kh-smoke-leader-")

    try:
        local = local_directory.name
        local_root = Path(local)
        database = local_root / "leader.sqlite"

        with socket.socket() as listener:
            listener.bind(("0.0.0.0", 0))
            port = listener.getsockname()[1]

        base = f"http://{arguments.leader_address}:{port}"
        with (evidence / "leader.log").open("wb") as log:
            leader_process = subprocess.Popen(
                [sys.executable, str(ROOT / "leader.py"), "serve", "--database", str(database),
                 "--listen", f"0.0.0.0:{port}", "--lease-seconds", "8", "--checkpoint-seconds", "0",
                 "--pin-leader-core"], stdout=log, stderr=log,
            )

        def ready() -> bool:
            """Wait for the private leader to accept HTTP requests."""

            try:
                return request(base, "/v1/health")["ok"]
            except OSError:
                return False

        wait(ready, "private leader startup", 10)

        for host in arguments.hosts:
            deployed.append(host)
            deploy(host, directory, archive)
            workers[host] = start_worker(host, directory, base, names[host], host != arguments.hosts[0])

        wait(lambda: len(request(base, "/v1/status")["nodes"]) == 3, "three remote registrations", 30)
        specification = {"program": "dp", "arguments": {
            "p": arguments.prime, "r": arguments.degree, "threads": 2, "tile_side": 512,
            "progress_milliseconds": 100, "max_state_bytes": 500_000_000, "max_visits": 30_000_000_000,
        }}
        queued = request(base, "/v1/enqueue", {"specification": specification})

        def run() -> dict[str, Any]:
            """Read this experiment's current run from private leader status."""

            return next(row for row in request(base, "/v1/status")["runs"] if row["run_id"] == queued["run_id"])

        def replicated() -> dict[str, Any] | None:
            """Require a partial, independently copied checkpoint before injecting worker loss."""

            row = run()

            if row["state"] in {"failed", "complete"}:
                raise RuntimeError(f"origin reached {row['state']} before a partial replicated checkpoint: {row.get('error')}")

            return row if 0 < row["replicated_checkpoint_done"] < row["progress_total"] else None

        durable = wait(replicated, "partial remote checkpoint replication")
        report["before_loss"] = durable
        print(f"replicated {durable['replicated_checkpoint_done']} cells; killing only the experiment's origin agent", flush=True)

        with sqlite3.connect(database) as connection:
            holders = {row[0] for row in connection.execute(
                "SELECT node_name FROM checkpoint_replicas WHERE manifest_hash=?",
                (durable["replicated_checkpoint_hash"],),
            )}

        target = next(host for host in arguments.hosts[1:] if names[host] in holders)
        origin = arguments.hosts[0]
        stop_worker(origin, directory, workers[origin])
        del workers[origin]
        stop_worker(target, directory, workers[target])
        workers[target] = start_worker(target, directory, base, names[target], False)

        def complete() -> dict[str, Any] | None:
            """Wait for the replacement to finish, preserving any visible failure diagnostic."""

            row = run()

            if row["state"] == "failed":
                raise RuntimeError(row["error"])

            return row if row["state"] == "complete" else None

        finished = wait(complete, "completion on another physical worker")

        if finished["node_name"] != names[target] or finished["restored_done"] <= 0:
            raise RuntimeError("replacement did not restore a nonempty checkpoint")

        report["after_recovery"] = finished
        report["replacement_host"] = target
        resumed = state_hashes(target, directory, queued["run_id"], finished["lease_token"])
        reference = local_root / "reference"
        artifact = local_root / "reference.json"
        subprocess.run(
            [str(ROOT.parent / "dp_solver" / "kh_dp_local"), str(arguments.prime), str(arguments.degree),
             "--work-dir", str(reference), "--tile-side", "512", "--threads", "2",
             "--max-visits", "30000000000", "--raw-transitions", "-o", str(artifact)],
            check=True, capture_output=True, timeout=90,
        )

        for name in ("values.bin", "choices.bin"):
            digest = hashlib.sha256()
            with (reference / name).open("rb") as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)

            if digest.hexdigest() != resumed[name]:
                raise RuntimeError(f"resumed {name} differs from complete raw DP")

        content = artifact.read_bytes()

        if hashlib.sha256(content).hexdigest() != resumed["artifact_hash"]:
            raise RuntimeError("resumed artifact differs from raw DP")

        (evidence / "result.json").write_bytes(content)
        report.update(verified=True, theta=json.loads(content)["theta"], full_state_hashes=resumed)

        # Wait for final output to survive another worker loss before cleanup.
        def final_replicated() -> dict[str, Any] | None:
            """Return status only after both surviving workers hold the final artifact."""

            status = request(base, "/v1/status")
            artifact_row = next(row for row in status["artifacts"] if row["artifact_hash"] == resumed["artifact_hash"])
            return status if artifact_row["replicas"] >= 2 else None

        report["final_status"] = wait(final_replicated, "final artifact replication to both surviving workers", 60)

        # Exercise retirement with the same agent-loss experiment, not just synthetic manifests.
        def retention_observed() -> dict[str, Any] | None:
            """Wait for a healthy worker to retire obsolete checkpoints without losing the result."""

            status = request(base, "/v1/status")
            row = next(row for row in status["runs"] if row["run_id"] == queued["run_id"])
            return status if row["retired_checkpoints"] > 0 else None

        report["final_status"] = wait(retention_observed, "retirement of old replicated checkpoints", 75)
        with sqlite3.connect(database) as connection:
            report["retention"] = dict(zip(("total", "retained", "retired"), connection.execute(
                "SELECT COUNT(*),SUM(retired_at IS NULL),SUM(retired_at IS NOT NULL) FROM checkpoints WHERE run_id=?",
                (queued["run_id"],),
            ).fetchone()))
        print(f"verified exact complete tables and artifact, theta={report['theta']}", flush=True)
    except Exception as error:
        report["error"] = str(error)
        print(f"cluster_smoke.py: {error}", file=sys.stderr)
    finally:
        cleanup_blocked: set[str] = set()

        for host, record in workers.items():
            try:
                stop_worker(host, directory, record)
            except Exception as error:
                report["cleanup"][host] = str(error)
                cleanup_blocked.add(host)

        for host in deployed:
            if host in cleanup_blocked:
                continue

            try:
                collected = collect_and_remove(host, directory)
                (evidence / f"{names[host]}.log").write_text(collected["log"])
                report["cleanup"][host] = "owned processes stopped and private directory removed"
            except Exception as error:
                report["cleanup"][host] = str(error)

        if leader_process is not None:
            leader_process.terminate()
            try:
                leader_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                leader_process.kill()
                leader_process.wait(timeout=5)

        local_directory.cleanup()
        report["elapsed_seconds"] = time.monotonic() - started
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"evidence: {evidence}", flush=True)

    cleanup_ok = all(value == "owned processes stopped and private directory removed" for value in report["cleanup"].values())
    return 0 if report.get("verified") and not report.get("error") and cleanup_ok else 1


# Empty invocations show help rather than contacting the household machines.
if __name__ == "__main__":
    raise SystemExit(main())
