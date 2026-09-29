#!/usr/bin/env python3
"""Resumable exact DP wave driver with immutable tile shards on local or SSH workers."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
from array import array
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import uuid

from blob_store import file_digest, sync_directory
from dp_solver.scheduling import dp_estimate
from dp_solver.tiles import build_halo, dependencies, tile

ROOT = Path(__file__).resolve().parent
BINARY = ROOT.parent / "dp_solver" / "kh_dp_tile"
STOP_REQUESTED = False
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


# Latch intentional stop and let only the active immutable tile batch finish.
def request_stop(signum, frame) -> None:
    """Record SIGINT/SIGTERM without interrupting a tile's durable publication boundary."""

    global STOP_REQUESTED
    STOP_REQUESTED = True


# Configure durable SQLite transactions for the small coordinator index.
def database(path: Path) -> sqlite3.Connection:
    """Open path with row access, WAL and durable commits; callers close the connection."""

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


# Invoke Python remotely with proper shell quoting rather than interpolated shell source.
def remote(host: str, code: str, arguments: list[str], timeout: float = 30) -> dict:
    """Execute code on host and return its JSON control result or a bounded diagnostic."""

    result = subprocess.run([*SSH, host, shlex.join(["python3", "-c", code, *arguments])],
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise OSError(f"{host}: {result.stderr.decode(errors='replace')[-2000:]}")
    return json.loads(result.stdout)


# Transfer one file with SSH while keeping Python's memory independent of file size.
def transfer(source: str, destination: str) -> None:
    """Copy a local/remote file using scp; errors leave the tile uncommitted."""

    result = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                             source, destination], capture_output=True, timeout=300)
    if result.returncode:
        raise OSError(result.stderr.decode(errors="replace")[-2000:])


# Deploy immutable solver tooling only inside this calculation's private remote root.
def prepare(host: str, root: str) -> None:
    """Create a private root on host and upload the C kernel and parent-death affinity wrapper."""

    facts = remote(host, "import pathlib,json,sys; p=pathlib.Path(sys.argv[1]); p.mkdir(exist_ok=True); print(json.dumps({'root':str(p),'byteorder':sys.byteorder}))", [root])
    if facts["byteorder"] != sys.byteorder:
        raise ValueError("SSH worker native byte order is incompatible")
    transfer(str(BINARY), f"{host}:{root}/kh_dp_tile")
    transfer(str(ROOT.parent / "cluster" / "affinity_exec.py"), f"{host}:{root}/affinity_exec.py")


# Fetch one immutable tile member into a bounded coordinator cache and verify its complete hash.
def fetch(record: dict, name: str, cache: Path) -> Path:
    """Return a verified local cache copy of record's named member; never trust just the filename."""

    digest = record[name + "_hash"]
    destination = cache / digest
    if destination.exists() and file_digest(destination) == digest:
        return destination
    failures = []
    for replica in record.get("copies", [record]):
        partial = cache / (digest + ".part-" + uuid.uuid4().hex)
        try:
            if replica["host"] == "local":
                with (Path(replica["location"]) / (name + ".bin")).open("rb") as source, partial.open("xb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
            else:
                transfer(f"{replica['host']}:{replica['location']}/{name}.bin", str(partial))
            if file_digest(partial) != digest:
                raise ValueError("tile member checksum mismatch")
            os.replace(partial, destination)
            return destination
        except (OSError, ValueError) as error:
            failures.append(str(error))
        finally:
            partial.unlink(missing_ok=True)
    raise OSError("no valid tile replica: " + "; ".join(failures))


# Compute on one worker at a time; each process's threads share exactly one bounded tile state.
def execute(host: str, worker_root: str, arguments: argparse.Namespace, target, halo: Path, cpus: list[int]) -> dict:
    """Compute target from halo; return its shard location and verified output hashes."""

    attempt = "tile-" + uuid.uuid4().hex
    command = [str(BINARY), str(arguments.p), str(arguments.r), str(target.first_u), str(target.last_u),
               str(target.first_v), str(target.last_v), str(halo), str(Path(worker_root) / attempt),
               str(arguments.threads), str(arguments.max_bytes)]
    if host == "local":
        command = [sys.executable, str(ROOT.parent / "cluster" / "affinity_exec.py"), "--parent-pid", str(os.getpid()),
                   "--cpus", ",".join(map(str, cpus)), "--", *command]
        with (Path(worker_root) / "compute.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with (Path(worker_root) / (attempt + ".log")).open("wb") as log:
                subprocess.run(command, stdout=log, stderr=log, check=True)
        location = Path(worker_root) / attempt
        return {"host": host, "location": str(location), "values_hash": file_digest(location / "values.bin"),
                "choices_hash": file_digest(location / "choices.bin")}

    remote_halo = worker_root + "/" + attempt + ".halo"
    transfer(str(halo), f"{host}:{remote_halo}")
    code = """import ctypes,fcntl,hashlib,json,os,pathlib,signal,subprocess,sys
parent=os.getppid()
if ctypes.CDLL(None).prctl(1,signal.SIGKILL,0,0,0)!=0: raise OSError('parent death signal failed')
if os.getppid()!=parent: raise RuntimeError('lost SSH supervisor')
root=pathlib.Path(sys.argv[1]); attempt=sys.argv[2]
lock=(root/'compute.lock').open('a+b'); fcntl.flock(lock,fcntl.LOCK_EX)
cpus=sorted(os.sched_getaffinity(0))[:int(sys.argv[9])]
command=[sys.executable,str(root/'affinity_exec.py'),'--parent-pid',str(os.getpid()),'--cpus',','.join(map(str,cpus)),'--',str(root/'kh_dp_tile'),*sys.argv[3:9],str(root/(attempt+'.halo')),str(root/attempt),sys.argv[9],sys.argv[10]]
try:
    with (root/(attempt+'.log')).open('wb') as log:
        subprocess.run(command,stdout=log,stderr=log,check=True)
    result={'host':sys.argv[11],'location':str(root/attempt)}
    for name in ('values','choices'):
        digest=hashlib.sha256()
        with (root/attempt/(name+'.bin')).open('rb') as source:
            while block:=source.read(1048576): digest.update(block)
        result[name+'_hash']=digest.hexdigest()
    print(json.dumps(result))
finally:
    (root/(attempt+'.halo')).unlink(missing_ok=True)
"""
    return remote(host, code, [worker_root, attempt, str(arguments.p), str(arguments.r), str(target.first_u),
                               str(target.last_u), str(target.first_v), str(target.last_v), str(arguments.threads),
                               str(arguments.max_bytes), host], timeout=86400)


# Retain two independently located tile shards before declaring their dependency usable.
def replicate(record: dict, host: str, root: str, cache: Path) -> dict:
    """Copy both members to host/root, verify full hashes there, and record a complete second copy."""

    values = fetch(record, "values", cache)
    choices = fetch(record, "choices", cache)
    location = root + "/replica-" + uuid.uuid4().hex
    if host == "local":
        destination = Path(location)
        destination.mkdir()
        for name, source in (("values", values), ("choices", choices)):
            with source.open("rb") as input_file, (destination / (name + ".bin")).open("xb") as output:
                while block := input_file.read(1024 * 1024):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
        sync_directory(destination)
        sync_directory(destination.parent)
    else:
        remote(host, "import pathlib,json,sys; pathlib.Path(sys.argv[1]).mkdir(); print(json.dumps({'ok':True}))", [location])
        transfer(str(values), f"{host}:{location}/values.bin")
        transfer(str(choices), f"{host}:{location}/choices.bin")
        code = """import hashlib,json,os,pathlib,sys
root=pathlib.Path(sys.argv[1])
for name,expected in (('values',sys.argv[2]),('choices',sys.argv[3])):
    digest=hashlib.sha256()
    with (root/(name+'.bin')).open('rb') as source:
        while block:=source.read(1048576): digest.update(block)
        os.fsync(source.fileno())
    if digest.hexdigest()!=expected: raise ValueError('replica hash mismatch')
for path in (root,root.parent):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY); os.fsync(descriptor); os.close(descriptor)
print(json.dumps({'ok':True}))
"""
        remote(host, code, [location, record["values_hash"], record["choices_hash"]])
    return {**record, "copies": [{"host": record["host"], "location": record["location"]},
                                  {"host": host, "location": location}]}


# Recover the compact split by fetching only tile choices along its predecessor path.
def artifact(arguments: argparse.Namespace, connection: sqlite3.Connection, cache: Path) -> dict:
    """Return the same ordered run-length DP document as the dense C prototype."""

    p, r = arguments.p, arguments.r
    estimate = dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})
    budget = estimate["budget"]
    u, v = budget, budget
    runs = []
    theta = None
    active_tile = None
    while u and v:
        row, column = (u - 1) // arguments.tile_side, (v - 1) // arguments.tile_side
        if active_tile != (row, column):
            for path in cache.iterdir():
                path.unlink()
            active_tile = (row, column)
        record = json.loads(connection.execute("SELECT result FROM tiles WHERE row=? AND column=?", (row, column)).fetchone()[0])
        rectangle = tile(p, r, arguments.tile_side, row, column)
        offset = (u - rectangle.first_u) * (rectangle.last_v - rectangle.first_v + 1) + v - rectangle.first_v
        if theta is None:
            with fetch(record, "values", cache).open("rb") as source:
                source.seek(offset * 8)
                value = array("Q")
                value.frombytes(source.read(8))
                theta = value[0]
        with fetch(record, "choices", cache).open("rb") as source:
            source.seek(offset * 4)
            value = array("I")
            value.frombytes(source.read(4))
            choice = value[0]
        if choice == 0:
            break
        if choice > p**3:
            raise ValueError("invalid tile reconstruction choice")
        index = choice - 1
        t = index % p + 1
        index //= p
        b = index % p + 1
        a = index // p + 1
        if a*t > u or b*t > v:
            raise ValueError("unaffordable reconstruction choice")
        if runs and (runs[-1]["a"], runs[-1]["b"], runs[-1]["t"]) == (a, b, t):
            runs[-1]["repeat"] += 1
        else:
            runs.append({"a": a, "b": b, "t": t, "repeat": 1})
        u -= a*t
        v -= b*t
    return {"format": "KHDP2-draft", "p": p, "r": r, "q": estimate["q"],
            "f": p**(r//2), "budget": budget, "theta": theta or 0, "runs": runs}


# Expose an explicit resumable driver without performing work on an empty invocation.
def build_parser() -> argparse.ArgumentParser:
    """Return the local/SSH tile driver parser and a complete command example."""

    parser = argparse.ArgumentParser(description="Compute exact DP as resumable immutable wave tiles.",
        epilog="Example: ./tile_driver.py 5 3 --work-dir /tmp/dp-tiles --tile-side 7 --workers 3 -o /tmp/dp.json")
    parser.add_argument("p", type=int, nargs="?")
    parser.add_argument("r", type=int, nargs="?")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--tile-side", type=int, default=4096)
    parser.add_argument("--workers", type=int, default=1, help="local worker count when no SSH hosts are supplied")
    parser.add_argument("--hosts", nargs="+", help="SSH workers, one tile at a time per distinct host")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-bytes", type=int, default=2*1024**3)
    parser.add_argument("--max-tiles", type=int, default=10000)
    parser.add_argument("--max-visits", type=int, default=5_000_000_000, help="conservative whole-calculation work admission")
    parser.add_argument("--stop-after-waves", type=int, default=0, help="test/review boundary; retains committed shards")
    parser.add_argument("-o", "--output", type=Path)
    return parser


# Execute independent tiles in parallel and commit each immutable result before the next wave.
def main() -> int:
    """Run a requested calculation or show help; durable index resumes after interruption."""

    global STOP_REQUESTED
    STOP_REQUESTED = False
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.p is None:
        parser.print_help()
        return 0
    if arguments.r is None or arguments.work_dir is None or arguments.output is None:
        parser.error("degree, work directory and output are required")
    if min(arguments.tile_side, arguments.workers, arguments.threads, arguments.max_bytes, arguments.max_tiles, arguments.max_visits) <= 0:
        parser.error("worker and resource controls must be positive")
    if arguments.output.exists():
        parser.error("output already exists; use a new output path")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    hosts = arguments.hosts or ["local"] * arguments.workers
    if arguments.hosts and (len(hosts) != len(set(hosts)) or any(not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in host) for host in hosts)):
        parser.error("SSH host names must be distinct simple names or IPv4 addresses")
    estimate = dp_estimate({"program": "dp", "arguments": {"p": arguments.p, "r": arguments.r}})
    if estimate["raw_visits"] > arguments.max_visits:
        parser.error("raw work bound exceeds --max-visits admission")
    count = (estimate["budget"] + arguments.tile_side - 1) // arguments.tile_side
    if count**2 > arguments.max_tiles:
        parser.error("tile index exceeds --max-tiles admission")
    allowed = sorted(os.sched_getaffinity(0))
    if not arguments.hosts and len(hosts)*arguments.threads > len(allowed):
        parser.error("local worker threads exceed available CPUs")
    arguments.work_dir.mkdir(parents=True, exist_ok=True)
    cache = arguments.work_dir / "cache"
    cache.mkdir(exist_ok=True)
    fingerprint = {"p": arguments.p, "r": arguments.r, "side": arguments.tile_side,
                   "hosts": hosts, "byteorder": sys.byteorder, "kernel": file_digest(BINARY)}

    # A coordinator lock prevents simultaneous drivers from assigning the same tile generation.
    with (arguments.work_dir / "driver.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with database(arguments.work_dir / "tiles.sqlite") as connection:
            connection.executescript("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS tiles(row INTEGER,column INTEGER,result TEXT,PRIMARY KEY(row,column));")
            previous = connection.execute("SELECT value FROM metadata WHERE key='specification'").fetchone()
            if previous and json.loads(previous[0]) != fingerprint:
                raise ValueError("resume parameters, worker locations or kernel changed")
            identifier = connection.execute("SELECT value FROM metadata WHERE key='identifier'").fetchone()
            identifier = identifier[0] if identifier else uuid.uuid4().hex
            connection.execute("INSERT OR IGNORE INTO metadata VALUES('identifier',?)", (identifier,))
            connection.execute("INSERT OR IGNORE INTO metadata VALUES('specification',?)", (json.dumps(fingerprint, sort_keys=True),))
            connection.commit()
            sync_directory(arguments.work_dir)
            worker_roots = []
            for index, host in enumerate(hosts):
                if host == "local":
                    worker_root = arguments.work_dir / f"worker-{index}"
                    worker_root.mkdir(exist_ok=True)
                    worker_roots.append(str(worker_root.resolve()))
                else:
                    worker_root = "/tmp/kh-tiled-" + identifier
                    prepare(host, worker_root)
                    worker_roots.append(worker_root)
            committed = 0
            completed_waves = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
                for wave in range(2*count - 1):
                    if STOP_REQUESTED:
                        return 75
                    pending = []
                    for row in range(count):
                        column = wave - row
                        if not 0 <= column < count:
                            continue
                        if connection.execute("SELECT 1 FROM tiles WHERE row=? AND column=?", (row, column)).fetchone():
                            continue
                        pending.append(tile(arguments.p, arguments.r, arguments.tile_side, row, column))
                    while pending:
                        batch = pending[:len(hosts)]
                        pending = pending[len(hosts):]
                        futures = {}
                        for index, target in enumerate(batch):
                            if target.halo_bytes > arguments.max_bytes:
                                raise ValueError("tile halo exceeds per-worker byte admission")
                            sources = {}
                            for dependency in dependencies(arguments.p, arguments.r, arguments.tile_side, target):
                                record = connection.execute("SELECT result FROM tiles WHERE row=? AND column=?", (dependency.row, dependency.column)).fetchone()
                                if record is None:
                                    raise RuntimeError("missing committed predecessor")
                                sources[(dependency.row, dependency.column)] = fetch(json.loads(record[0]), "values", cache)
                            halo = arguments.work_dir / f"halo-{target.row}-{target.column}-{uuid.uuid4().hex}.bin"
                            build_halo(arguments.p, arguments.r, arguments.tile_side, target, sources, halo, arguments.max_bytes)
                            cpus = allowed[index*arguments.threads:(index+1)*arguments.threads]
                            future = pool.submit(execute, hosts[index], worker_roots[index], arguments, target, halo, cpus)
                            futures[future] = (target, halo, index)
                        while futures:
                            done, _ = concurrent.futures.wait(futures, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED)
                            if not done:
                                print(json.dumps({"phase": "computing", "wave": wave, "committed_tiles": committed}), flush=True)
                            for future in done:
                                target, halo, origin_index = futures.pop(future)
                                failures = []
                                try:
                                    result = future.result()
                                except (OSError, subprocess.SubprocessError) as error:
                                    failures.append(str(error))
                                    result = None
                                    for peer in range(len(hosts)):
                                        if peer == origin_index:
                                            continue
                                        try:
                                            cpus = allowed[peer*arguments.threads:(peer+1)*arguments.threads]
                                            result = execute(hosts[peer], worker_roots[peer], arguments, target, halo, cpus)
                                            origin_index = peer
                                            break
                                        except (OSError, subprocess.SubprocessError) as replacement_error:
                                            failures.append(str(replacement_error))
                                    if result is None:
                                        raise OSError("all tile workers failed: " + "; ".join(failures))
                                if len(hosts) >= 2:
                                    replicated = None
                                    for offset in range(1, len(hosts)):
                                        peer = (origin_index + offset) % len(hosts)
                                        try:
                                            replicated = replicate(result, hosts[peer], worker_roots[peer], cache)
                                            break
                                        except (OSError, subprocess.SubprocessError) as error:
                                            failures.append(str(error))
                                    if replicated is None:
                                        raise OSError("no independent tile replica: " + "; ".join(failures))
                                    result = replicated
                                if failures:
                                    result["attempt_failures"] = failures
                                connection.execute("INSERT INTO tiles VALUES(?,?,?)", (target.row, target.column, json.dumps(result)))
                                connection.commit()
                                halo.unlink()
                                committed += 1
                                print(json.dumps({"phase": "committed", "row": target.row, "column": target.column, "host": result["host"]}), flush=True)
                        for path in cache.iterdir():
                            path.unlink()
                        if STOP_REQUESTED:
                            return 75
                    completed_waves += 1
                    if arguments.stop_after_waves and completed_waves >= arguments.stop_after_waves:
                        return 75
            document = artifact(arguments, connection, cache)
            temporary = arguments.output.with_name(arguments.output.name + ".tmp-" + uuid.uuid4().hex)
            with temporary.open("x") as output:
                json.dump(document, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.link(temporary, arguments.output)
            temporary.unlink()
            sync_directory(arguments.output.parent)
            print(json.dumps({"phase": "complete", "theta": document["theta"], "output": str(arguments.output)}), flush=True)
    return 0


# Errors retain the index and immutable shards for a reviewed resume.
if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"tile_driver.py: {error}", file=sys.stderr)
        raise SystemExit(1)
