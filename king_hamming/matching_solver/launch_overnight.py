#!/usr/bin/env python3
"""Start a retained, separate household campaign for saved DP matching attempts."""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from deployment import bundle, deploy, remote, request, wait
from matching_solver.artifacts import load_dp, request_count
from matching_solver.polynomials import first_primitive
from matching_solver.submit import specification

THREADS = 2
MAX_BYTES = 2**31

RESULTS = ROOT / "cluster/deployments/dp-campaign/results"
STATE = ROOT / "cluster/deployments/match-overnight"
HOSTS = [f"192.168.4.{number}" for number in range(101, 109)]
LEADER = "http://192.168.4.151:8051"


# Retain each successful deployment and submission as an atomic local record.
def save(path: Path, value: dict) -> None:
    """Replace the persistent campaign manifest at path."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


# Mirror native admission so no queued field is guaranteed to exceed the worker cap.
def memory_required(dp: dict, threads: int = THREADS) -> int:
    """Return the native kernel's conservative address-space requirement."""
    q = dp["q"]
    count = request_count(dp)
    descriptors = 24 * len(dp["runs"])
    reserve = 67_108_864
    stacks = 8_388_608 * threads
    state = 8 * q + 24 * count + (q + 7) // 8 + descriptors + stacks + reserve
    if threads > 1:
        state += 4 * count + 8 * ((q + 63) // 64)
    field = 4 * q + 4 * dp["budget"] * threads + stacks + descriptors + reserve
    return max(state, field)


# Keep the overnight frontier within native single-node memory and work bounds.
def candidates(max_q: int = 20_000_000, max_edges: int = 32_000_000_000) -> list[tuple[int, Path, dict]]:
    """Return admitted saved DP artifacts sorted by implicit edge scans."""
    entries = []
    for filename in glob.glob(str(RESULTS / "*.khdp")):
        path = Path(filename)
        dp, _ = load_dp(path)
        edges = request_count(dp) * dp["f"]
        if dp["q"] <= max_q and edges <= max_edges and memory_required(dp) <= MAX_BYTES:
            entries.append((edges, path, dp))
    return sorted(entries, key=lambda item: (item[0], item[2]["q"]))


# Each new agent has a private persistent tree and only two low-priority CPUs.
def launch_worker(host: str, directory: str, name: str, port: int = 0) -> dict:
    """Start and identify one detached matching agent on host."""
    code = '''import json,os,pathlib,shlex,socket,subprocess,sys
root=pathlib.Path(sys.argv[1]); leader=sys.argv[2]; host=sys.argv[3]; name=sys.argv[4]
with socket.socket() as listener:
    listener.bind(('0.0.0.0',int(sys.argv[5])))
    port=listener.getsockname()[1]
command=[sys.executable,str(root/'cluster'/'agent.py'),'run','--leader',leader,
    '--name',name,'--cpus','0,1','--work-root',str(root/'work'),
    '--storage-root',str(root/'blobs'),'--storage-listen',f'0.0.0.0:{port}',
    '--storage-url',f'http://{host}:{port}']
os.nice(10)
with (root/'agent.log').open('ab') as log:
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                             start_new_session=True)
start=pathlib.Path(f'/proc/{process.pid}/stat').read_text().split()[21]
print(json.dumps({'host':host,'root':str(root),'name':name,'pid':process.pid,
                  'start':start,'port':port,'command':shlex.join(command)}))
'''
    return remote(host, code, [directory, LEADER, host, name, str(port)])


# Launch a new independent leader and agents, then queue all admitted saved fields.
def start() -> None:
    """Create the persistent campaign without altering the DP deployment."""
    if STATE.exists():
        raise ValueError(f"{STATE} already exists; refusing to replace its index")
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 8051))
    entries = candidates()
    if not entries:
        raise ValueError("no saved DP artifacts pass overnight admission")
    for host in HOSTS:
        remote(host, "import json; print(json.dumps({'ok':True}))", [])
    archive = bundle()
    STATE.mkdir(parents=True)
    manifest_path = STATE / "manifest.json"
    manifest = {"id": "match-" + uuid.uuid4().hex, "leader": LEADER,
                "state": "starting", "workers": [], "entries": [],
                "source": str(RESULTS), "max_q": 20_000_000,
                "max_edges": 32_000_000_000, "max_bytes": MAX_BYTES}
    save(manifest_path, manifest)
    command = [sys.executable, str(ROOT / "cluster/leader.py"), "serve",
               "--database", str(STATE / "leader.sqlite"), "--listen",
               "0.0.0.0:8051", "--checkpoint-seconds", "1800",
               "--pin-leader-core"]
    with (STATE / "leader.log").open("ab") as log:
        leader = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=log, start_new_session=True)
    manifest["leader_process"] = {"pid": leader.pid,
        "start": Path(f"/proc/{leader.pid}/stat").read_text().split()[21],
        "command": shlex.join(command)}
    save(manifest_path, manifest)
    def leader_ready():
        """Wait for the detached matching leader to accept requests."""
        try:
            return request(LEADER, "/v1/health").get("ok")
        except OSError:
            return False
    wait(leader_ready, "matching leader", 20)
    for index, host in enumerate(HOSTS):
        directory = str(Path.home() / ".local/share/king_hamming" / manifest["id"])
        deploy(host, directory, archive)
        worker = launch_worker(host, directory, f"match-{index + 101}")
        manifest["workers"].append(worker)
        save(manifest_path, manifest)
    wait(lambda: len(request(LEADER, "/v1/status")["nodes"]) == len(HOSTS),
         "matching workers", 40)
    for edges, path, dp in entries:
        polynomial = first_primitive(dp["p"], dp["r"], dp["q"])
        job = specification(path, ",".join(map(str, polynomial)), THREADS, MAX_BYTES)
        result = request(LEADER, "/v1/enqueue", {"specification": job})
        manifest["entries"].append({"p": dp["p"], "r": dp["r"], "q": dp["q"],
                                    "edges": edges, "poly": polynomial,
                                    "dp": str(path), **result})
        save(manifest_path, manifest)
    manifest["state"] = "running"
    save(manifest_path, manifest)
    print(json.dumps({"leader": LEADER, "workers": len(manifest["workers"]),
                      "queued": len(manifest["entries"]),
                      "manifest": str(manifest_path)}, indent=2))


# Add newly collected fields to a retained campaign without restarting any process.
def extend(max_q: int, max_edges: int) -> None:
    """Queue saved DP fields within explicit limits that are not yet indexed."""
    with (STATE / "manifest.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _extend(max_q, max_edges)


def _extend(max_q: int, max_edges: int) -> None:
    """Update one campaign frontier while holding the manifest lock."""
    manifest_path = STATE / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    known = {(item["p"], item["r"]) for item in manifest["entries"]}
    added = []
    for edges, path, dp in candidates(max_q, max_edges):
        field = (dp["p"], dp["r"])
        if field in known:
            continue
        polynomial = first_primitive(dp["p"], dp["r"], dp["q"])
        job = specification(path, ",".join(map(str, polynomial)), THREADS, MAX_BYTES)
        result = request(LEADER, "/v1/enqueue", {"specification": job})
        manifest["entries"].append({"p": dp["p"], "r": dp["r"], "q": dp["q"],
                                    "edges": edges, "poly": polynomial,
                                    "dp": str(path), **result})
        save(manifest_path, manifest)
        known.add(field)
        added.append(f"{dp['p']}^{dp['r']}")
    manifest["max_bytes"] = MAX_BYTES
    manifest["max_q"] = max(manifest.get("max_q", 0), max_q)
    manifest["max_edges"] = max(manifest.get("max_edges", 0), max_edges)
    save(manifest_path, manifest)
    print(json.dumps({"added": added, "total": len(manifest["entries"])}))


# Reattach only missing owned processes; leave live matching computations alone.
def repair() -> dict:
    """Restart a dead leader or agent using retained manifest identities."""
    with (STATE / "manifest.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = STATE / "manifest.json"
        manifest = json.loads(path.read_text())
        recovered = []
        errors = []
        leader = manifest["leader_process"]
        proc = Path(f"/proc/{leader['pid']}/stat")
        alive = proc.exists() and proc.read_text().split()[21] == leader["start"]
        if not alive:
            command = shlex.split(leader["command"])
            with (STATE / "leader.log").open("ab") as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, start_new_session=True)
            leader["pid"] = process.pid
            leader["start"] = Path(f"/proc/{process.pid}/stat").read_text().split()[21]
            save(path, manifest)
            recovered.append("leader")
        def leader_ready() -> bool:
            """Return whether the current leader accepts health requests."""
            try:
                return bool(request(LEADER, "/v1/health").get("ok"))
            except OSError:
                return False
        wait(leader_ready, "matching leader", 20)
        code = '''import json,pathlib,sys
p=pathlib.Path('/proc')/sys.argv[1]/'stat'
try:
    fields=p.read_text().split()
    alive=fields[21]==sys.argv[2] and fields[2]!='Z'
except OSError:
    alive=False
print(json.dumps({'alive':alive}))
'''
        for worker in manifest["workers"]:
            try:
                status = remote(worker["host"], code, [str(worker["pid"]), worker["start"]])
                if status["alive"]:
                    continue
                replacement = launch_worker(worker["host"], worker["root"], worker["name"], worker["port"])
                worker.update(replacement)
                save(path, manifest)
                recovered.append(worker["name"])
            except (OSError, RuntimeError, ValueError) as error:
                errors.append(f"{worker['name']}: {error}")
        return {"recovered": recovered, "errors": errors}


# Print useful commands without depending on a live leader.
def main() -> int:
    """Parse start command or display a runnable help example."""
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 king_hamming/matching_solver/launch_overnight.py start"))
    parser.add_argument("command", nargs="?", choices=["start", "extend", "repair"])
    parser.add_argument("--max-q", type=int, default=20_000_000)
    parser.add_argument("--max-edges", type=int, default=32_000_000_000)
    arguments = parser.parse_args()
    if arguments.command is None:
        parser.print_help()
        return 0
    if arguments.max_q < 1 or arguments.max_edges < 1:
        parser.error("admission limits must be positive")
    if arguments.command == "start":
        start()
    elif arguments.command == "extend":
        extend(arguments.max_q, arguments.max_edges)
    else:
        outcome = repair()
        print(json.dumps(outcome))
        return 1 if outcome["errors"] else 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"launch_overnight.py: {error}", file=sys.stderr)
        raise SystemExit(1)
