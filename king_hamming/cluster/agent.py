#!/usr/bin/env python3
"""Run a first-draft king_hamming node agent."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import selectors
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from common import calculation_id, canonical_json, store_blob
from blob_store import blob_path, fetch_blob, file_digest, storage_transaction, sync_directory
import adapters
import gpus
from outcomes import SolverOutcome, classify
from checkpoints import DEFAULT_MAX_BYTES, capture_checkpoint, fetch_checkpoint, restore_checkpoint, validate_manifest


# Send a bounded JSON control request.
def request_json(leader: str, path: str, value: dict[str, Any]) -> dict[str, Any]:
    """POST value to a leader endpoint and decode the response."""

    body = canonical_json(value)
    request = Request(f"{leader.rstrip('/')}{path}", data=body, method="POST")
    request.add_header("Content-Type", "application/json")

    with urlopen(request, timeout=15) as response:
        result = json.load(response)

    if not isinstance(result, dict):
        raise ValueError("leader returned a non-object response")

    return result


# Read Linux topology rather than guessing from CPU numbering.
def cpu_topology() -> list[tuple[int, int, int]]:
    """Return available CPUs as (package, core, logical_cpu) tuples."""

    topology: list[tuple[int, int, int]] = []

    for cpu in sorted(os.sched_getaffinity(0)):
        root = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")

        try:
            package = int((root / "physical_package_id").read_text())
            core = int((root / "core_id").read_text())
        except (FileNotFoundError, ValueError):
            package = 0
            core = cpu

        topology.append((package, core, cpu))

    return topology


# Order one thread per physical core before SMT siblings.
def ordered_cpus(reserve_leader_core: bool) -> list[int]:
    """Return logical CPUs in physical-core-first placement order."""

    cores: dict[tuple[int, int], list[int]] = {}

    for package, core, cpu in cpu_topology():
        cores.setdefault((package, core), []).append(cpu)

    ordered_keys = sorted(cores)

    # Reserve both siblings of the first physical core on merlin.
    if reserve_leader_core and ordered_keys:
        ordered_keys = ordered_keys[1:]

    result: list[int] = []
    maximum_siblings = max((len(cpus) for cpus in cores.values()), default=0)

    for sibling_index in range(maximum_siblings):
        for key in ordered_keys:
            siblings = sorted(cores[key])

            if sibling_index < len(siblings):
                result.append(siblings[sibling_index])

    return result


# Parse a user-provided Linux CPU list.
def parse_cpu_list(value: str) -> list[int]:
    """Parse comma-separated CPUs and inclusive ranges."""

    result: set[int] = set()

    for item in value.split(","):
        if "-" in item:
            lower, upper = (int(part) for part in item.split("-", 1))
            result.update(range(lower, upper + 1))
        elif item:
            result.add(int(item))

    available = os.sched_getaffinity(0)

    if not result or not result.issubset(available):
        raise ValueError("CPU list is empty or includes an unavailable CPU")

    return sorted(result)


def process_group_usage(group: int) -> tuple[int, int] | None:
    """Return Linux process-group CPU microseconds and resident bytes, tolerating races."""
    ticks = os.sysconf("SC_CLK_TCK")
    page_size = os.sysconf("SC_PAGE_SIZE")
    cpu_ticks = rss_pages = 0
    found = False
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != group:
                continue
            cpu_ticks += int(fields[11]) + int(fields[12])
            rss_pages += max(0, int(fields[21]))
            found = True
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            continue
    if not found:
        return None
    return cpu_ticks * 1_000_000 // ticks, rss_pages * page_size


def storage_generation(root: Path) -> str:
    """Return a durable UUID identifying this storage tree, creating it once."""
    path = root / ".kh-storage-generation"
    try:
        with path.open("x") as output:
            value = str(uuid.uuid4())
            output.write(value + "\n")
            output.flush()
            os.fsync(output.fileno())
        sync_directory(root)
    except FileExistsError:
        value = path.read_text().strip()
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise ValueError("invalid retained storage generation file") from error


# Build a read-only content-addressed blob server.
def make_storage_handler(storage_root: Path, leader: str | None = None,
                         node_record: dict[str, Any] | None = None,
                         cpus: list[int] | None = None) -> type[BaseHTTPRequestHandler]:
    """Return an HTTP handler serving verified local blobs."""

    peer_lock = threading.Lock()
    peer_workers: set[int] = set()

    class StorageHandler(BaseHTTPRequestHandler):
        """Serve content-addressed artifacts from one node."""

        # Return a blob only when its digest has the exact expected shape.
        def do_GET(self) -> None:
            """Handle a content-addressed blob download."""

            prefix = "/blobs/"

            if not self.path.startswith(prefix):
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            digest = self.path[len(prefix):]

            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return

            path = storage_root / digest[:2] / digest[2:]

            if not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            size = path.stat().st_size
            offset = 0
            requested = self.headers.get("Range")

            if requested is not None:
                if not requested.startswith("bytes=") or not requested.endswith("-") or not requested[6:-1].isdigit():
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return

                offset = int(requested[6:-1])

                if offset >= size:
                    self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    return

            self.send_response(HTTPStatus.PARTIAL_CONTENT if requested else HTTPStatus.OK)
            self.send_header("Content-Length", str(size - offset))
            self.send_header("Accept-Ranges", "bytes")

            if requested:
                self.send_header("Content-Range", f"bytes {offset}-{size - 1}/{size}")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

            with path.open("rb") as input_file:
                input_file.seek(offset)

                while True:
                    block = input_file.read(1024 * 1024)

                    if not block:
                        break

                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError, ConnectionResetError):
                        return

        # A reserved peer gets one raw, bounded-lifetime stream to a trusted adapter worker.
        def do_CONNECT(self) -> None:
            """Authorize this node's reservation and bridge one solver worker pipe."""
            if self.path != "/v1/peer" or leader is None or node_record is None or cpus is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            run_id = self.headers.get("X-Run-Id", "")
            lease_token = self.headers.get("X-Lease-Token", "")
            try:
                worker_index = int(self.headers.get("X-Worker-Index", ""))
                worker_count = int(self.headers.get("X-Worker-Count", ""))
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            if (len(run_id) != 36 or len(lease_token) != 36 or
                    not 1 <= worker_index < worker_count <= 256):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            identity = {"node_name": node_record["node_name"],
                        "session_id": node_record["session_id"],
                        "run_id": run_id, "lease_token": lease_token,
                        "worker_index": worker_index, "worker_count": worker_count}
            try:
                authorized = request_json(leader, "/v1/peer-authorize", identity)
                command = adapters.get(authorized["specification"]).peer_command(
                    authorized["specification"], cpus,
                    authorized["worker_index"], authorized["worker_count"])
            except (HTTPError, OSError, ValueError, KeyError) as error:
                self.send_error(HTTPStatus.CONFLICT, str(error))
                return
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # One reserved node serves one shard process using its assigned CPU set.
            with peer_lock:
                if worker_index in peer_workers:
                    self.send_error(HTTPStatus.CONFLICT, "peer worker shard is already active")
                    return
                peer_workers.add(worker_index)
            try:
                child = subprocess.Popen(command, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=sys.stderr,
                                         start_new_session=True)
            except OSError:
                with peer_lock:
                    peer_workers.discard(worker_index)
                raise
            closed = threading.Event()
            self.send_response(HTTPStatus.OK, "Connection Established")
            self.end_headers()
            self.wfile.flush()

            def input_pump() -> None:
                """Copy framed coordinator requests into the worker's stdin."""
                assert child.stdin is not None
                try:
                    while not closed.is_set():
                        chunk = os.read(self.connection.fileno(), 65536)
                        if not chunk:
                            break
                        child.stdin.write(chunk)
                        child.stdin.flush()
                except (BrokenPipeError, OSError, ValueError):
                    pass
                finally:
                    try:
                        child.stdin.close()
                    except OSError:
                        pass

            def fence_monitor() -> None:
                """Terminate a peer worker promptly when its group lease is fenced."""
                deadline = time.monotonic() + 60
                next_resource = time.monotonic() + 10
                while not closed.wait(1):
                    try:
                        request_json(leader, "/v1/peer-authorize", identity)
                        deadline = time.monotonic() + 60
                    except HTTPError as error:
                        if error.code == 409:
                            break
                    except OSError:
                        pass
                    if time.monotonic() >= next_resource:
                        usage = process_group_usage(child.pid)
                        if usage is not None:
                            try:
                                request_json(leader, "/v1/resource-usage", {
                                    "run_id": run_id, "lease_token": lease_token,
                                    "component": "shard", "shard_index": worker_index,
                                    "cpu_microseconds": usage[0], "peak_rss_bytes": usage[1],
                                })
                            except (HTTPError, OSError):
                                pass
                        next_resource = time.monotonic() + 10
                    if time.monotonic() >= deadline:
                        break
                if not closed.is_set() and child.poll() is None:
                    child.terminate()
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

            input_thread = threading.Thread(target=input_pump, daemon=True)
            monitor_thread = threading.Thread(target=fence_monitor, daemon=True)
            input_thread.start()
            monitor_thread.start()
            try:
                assert child.stdout is not None
                while True:
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    self.connection.sendall(chunk)
            except (BrokenPipeError, OSError):
                pass
            finally:
                closed.set()
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                input_thread.join(timeout=1)
                monitor_thread.join(timeout=1)
                self.close_connection = True
                with peer_lock:
                    peer_workers.discard(worker_index)

        # Suppress routine access messages.
        def log_message(self, format_string: str, *arguments: Any) -> None:
            """Suppress the base HTTP access log."""

            return

    return StorageHandler


# Keep the node visible even while a solver is active.
def heartbeat_loop(
    leader: str,
    node_record: dict[str, Any],
    stop_event: threading.Event,
    interval: float = 10.0,
) -> None:
    """Send node heartbeats until stop_event is set."""

    while not stop_event.wait(interval):
        try:
            free = shutil.disk_usage(node_record["storage_root"]).free
            request_json(leader, "/v1/heartbeat", {**node_record, "storage_free_bytes": free})
        except OSError as error:
            print(f"heartbeat failed: {error}", file=sys.stderr, flush=True)


# Convert one leased specification into a solver command.
def solver_command(specification, output, checkpoint, checkpoint_seconds):
    """Return trusted adapter argv for specification, files and checkpoint interval."""

    return adapters.get(specification).command(specification, output, checkpoint, checkpoint_seconds)


# A revoked lease is an ownership change, never an ordinary solver failure.
class LeaseLost(RuntimeError):
    """Signal that this supervisor must cease all work for an old lease."""


# Renew ownership independently of hashing, restore I/O, and solver output.
class LeaseKeeper:
    """Maintain a monotonic local deadline and the leader's latched stop state."""

    def __init__(self, leader: str, job: dict[str, Any], interval: float) -> None:
        """Initialize from leader/job and requested poll interval; perform no I/O."""

        self.leader = leader
        self.identity = {"run_id": job["run_id"], "lease_token": job["lease_token"]}
        self.seconds = float(job.get("lease_seconds", 60))
        self.interval = min(interval, self.seconds / 3)
        self.deadline = time.monotonic() + self.seconds
        self.stopped = False
        self.lost = False
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def renew(self) -> None:
        """Renew the lease once; update state conservatively using request start time."""

        started = time.monotonic()
        response = request_json(self.leader, "/v1/run-control", self.identity)

        with self.lock:
            self.seconds = float(response.get("lease_seconds", self.seconds))
            self.deadline = started + self.seconds
            self.stopped = self.stopped or bool(response.get("stop_requested", False))

    def start(self) -> None:
        """Confirm ownership before launching work, then start the renewal thread."""

        self.renew()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self) -> None:
        """Retry transient network errors until the conservative deadline expires."""

        while not self.closed.wait(self.interval):
            try:
                self.renew()
            except HTTPError as error:
                if error.code == 409:
                    self.lost = True
                    return
            except OSError:
                pass

            if time.monotonic() >= self.deadline:
                self.lost = True
                return

    def check(self) -> None:
        """Raise LeaseLost when ownership is revoked, expired, or this keeper is closed."""

        with self.lock:
            if self.lost or self.closed.is_set() or time.monotonic() >= self.deadline:
                raise LeaseLost("computation lease expired or was reassigned")

    def control(self) -> dict[str, Any]:
        """Return cached stop control after checking current lease ownership."""

        self.check()
        return {"stop_requested": self.stopped}

    def close(self) -> None:
        """Stop renewals; a delayed in-flight response cannot authorize new work."""

        self.closed.set()

        if self.thread is not None:
            self.thread.join(timeout=0.2)


# Tell the leader only about confirmed bad content, preserving transient outage tolerance.
def bad_blob_reporter(
    leader: str, identity: dict[str, Any], peers: list[dict[str, Any]],
) -> Callable[[str | None, str], None]:
    """Return a callback dropping replica claims for a peer's confirmed damaged blob."""

    def report(location: str | None, digest: str) -> None:
        """Map location to a registered peer; None denotes this agent's own corrupt cache."""

        source = identity.get("node_name") if location is None else next(
            (peer["node_name"] for peer in peers if location == f"{peer['address'].rstrip('/')}/blobs/{digest}"), None,
        )

        if source is None:
            return

        try:
            request_json(leader, "/v1/blob-bad", {
                **identity, "source_node": source, "blob_hash": digest,
            })
        except OSError as error:
            print(f"could not report damaged replica: {error}", file=sys.stderr, flush=True)

    return report


# Copy a whole checkpoint or final artifact directly between storage workers.
def replicate_once(
    leader: str,
    node_name: str,
    storage_root: Path,
    storage_url: str,
    session_id: str = "",
) -> bool:
    """Verify one replica before acknowledging it; return whether work was performed."""

    with storage_transaction(storage_root):
        identity = {"node_name": node_name, "session_id": session_id}
        batch = request_json(leader, "/v1/revalidation-batch", identity)
        records = batch.get("records", [])
        if records:
            mode = batch.get("mode")
            if mode not in {"metadata", "hash"}:
                raise ValueError("leader returned invalid storage validation mode")
            checked: dict[tuple[str, int, str], bool] = {}
            results = []
            for record in records:
                valid = True
                for member in record["members"]:
                    digest, size = member["digest"], member["size"]
                    key = (digest, size, mode)
                    if key not in checked:
                        path = blob_path(storage_root, digest)
                        checked[key] = bool(
                            path.is_file() and path.stat().st_size == size and
                            (mode == "metadata" or file_digest(path) == digest))
                    valid = valid and checked[key]
                results.append({"kind": record["kind"], "digest": record["digest"],
                                "valid": valid})
            request_json(leader, "/v1/revalidation-batch-done",
                         {**identity, "records": results})
            return True
        response = request_json(leader, "/v1/replication", identity)
        task = response.get("replication")

        if task is None:
            return False

        if task.get("kind") in {"revalidate_checkpoint", "revalidate_artifact"}:
            checkpoint_inventory = task["kind"] == "revalidate_checkpoint"
            digest = task["manifest_hash"] if checkpoint_inventory else task["artifact_hash"]
            try:
                if checkpoint_inventory:
                    manifest = task["manifest"]
                    validate_manifest(manifest, task["specification"], manifest["run_id"], int(task["max_checkpoint_bytes"]))
                    fetch_checkpoint(task, storage_root)
                    request_json(leader, "/v1/checkpoint-replica", {**identity, "manifest_hash": digest})
                else:
                    path = blob_path(storage_root, digest)
                    if not path.exists() or (task.get("size") is not None and path.stat().st_size != task["size"]) or file_digest(path) != digest:
                        raise ValueError("retained artifact is missing or damaged")
                    request_json(leader, "/v1/replica", {**identity, "artifact_hash": digest, "location": f"{storage_url.rstrip('/')}/blobs/{digest}"})
            except (OSError, ValueError) as error:
                print(f"retained replica not acknowledged: {error}", file=sys.stderr, flush=True)
            request_json(leader, "/v1/revalidate-done", {**identity, "kind": "checkpoint" if checkpoint_inventory else "artifact", "digest": digest})
            return True

        if task.get("kind") == "checkpoint":
            manifest = task["manifest"]
            validate_manifest(manifest, task["specification"], manifest["run_id"],
                              int(task.get("max_checkpoint_bytes", DEFAULT_MAX_BYTES)))
            reporter = bad_blob_reporter(leader, identity, task["sources"])
            fetch_checkpoint(task, storage_root, bad_source=reporter)
            request_json(leader, "/v1/checkpoint-replica", {**identity, "manifest_hash": task["manifest_hash"]})
        else:
            digest = str(task["artifact_hash"])
            size = task.get("size")

            # Older databases did not record artifact sizes; learn that bounded transfer size once.
            if size is None:
                with urlopen(task["location"], timeout=5) as response:
                    size = int(response.headers["Content-Length"])

            fetch_blob(storage_root, digest, int(size), task.get("locations", [task["location"]]))
            location = f"{storage_url.rstrip('/')}/blobs/{digest}"
            request_json(leader, "/v1/replica", {**identity, "artifact_hash": digest, "location": location})

        return True


# Delete only leader-authorized obsolete objects while publication on this worker is excluded.
def collect_garbage(
    leader: str, node_record: dict[str, Any], storage_root: Path,
) -> int:
    """Collect one bounded retired-blob batch; return removed bytes and keep history intact."""

    with storage_transaction(storage_root):
        return collect_garbage_locked(leader, node_record, storage_root)


# Call only while the worker storage transaction excludes new local references.
def collect_garbage_locked(
    leader: str, node_record: dict[str, Any], storage_root: Path,
) -> int:
    """Remove one planned batch under an already-held lock; return durable bytes removed."""

    identity = {"node_name": node_record["node_name"], "session_id": node_record["session_id"]}
    removed = 0

    plan = request_json(leader, "/v1/gc-plan", identity)
    completed: list[str] = []
    directories: set[Path] = set()

    for digest in plan["blob_hashes"]:
        path = blob_path(storage_root, digest)

        if path.exists():
            size = path.stat().st_size
            path.unlink()
            directories.add(path.parent)
            removed += size

        completed.append(digest)

    for directory in directories:
        sync_directory(directory)

    if completed:
        request_json(leader, "/v1/gc-done", {**identity, "blob_hashes": completed})
        print(f"retired {len(completed)} obsolete checkpoint objects ({removed} bytes)", flush=True)

    return removed


# Replication continues while every worker is occupied with mathematical work.
def replication_loop(
    leader: str, node_record: dict[str, Any], storage_root: Path,
    stop_event: threading.Event, interval: float,
) -> None:
    """Repair replicas in bounded serial transfers until stop_event is set."""

    next_collection = 0.0

    while not stop_event.is_set():
        try:
            if time.monotonic() >= next_collection:
                collect_garbage(leader, node_record, storage_root)
                next_collection = time.monotonic() + 60.0

            worked = replicate_once(leader, node_record["node_name"], storage_root,
                                    node_record["address"], node_record["session_id"])
        except (OSError, ValueError, KeyError) as error:
            print(f"replication will retry: {error}", file=sys.stderr, flush=True)
            worked = False

        stop_event.wait(0.01 if worked else interval)


# Supervise independent control polling while draining both child pipes.
def supervise_solver(
    command: list[str],
    leader: str,
    job: dict[str, Any],
    control_seconds: float,
    stop_grace_seconds: float,
    keeper: LeaseKeeper | None = None,
    snapshot: Callable[[int, threading.Event], None] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one child; return exit, intentional-stop, bounded diagnostics, and progress."""

    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.PIPE if snapshot is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    cancelled = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1) if snapshot is not None else None
    pending_snapshot: concurrent.futures.Future[None] | None = None
    stopped = False
    forced = False
    stop_deadline: float | None = None
    next_control = time.monotonic()
    next_resource = time.monotonic() + 10
    stdout_buffer = bytearray()
    stderr_tail = bytearray()
    latest: dict[str, Any] = {"done": 0, "total": 0, "checkpoint_done": 0}

    # Forward genuine solver reports without synthesizing solver heartbeat data.
    def forward_line(line: bytes) -> None:
        """Validate a solver JSON line and forward its status to the leader."""

        nonlocal latest, pending_snapshot
        if len(line) > 1024 * 1024:
            raise ValueError("solver progress record exceeds 1 MiB")

        progress = json.loads(line)

        if not isinstance(progress, dict):
            raise ValueError("solver progress must be a JSON object")

        if progress.get("event") == "checkpoint":
            if executor is None or snapshot is None or pending_snapshot is not None:
                raise ValueError("unexpected or overlapping checkpoint handshake")

            pending_snapshot = executor.submit(snapshot, int(progress["cursor"]), cancelled)
            return

        if progress.get("event") == "resource_usage":
            try:
                request_json(
                    leader, "/v1/resource-usage",
                    {**progress, "run_id": job["run_id"], "lease_token": job["lease_token"]},
                )
            except HTTPError as error:
                if error.code == 409:
                    raise LeaseLost("resource usage rejected for stale lease") from error
                raise
            except OSError:
                if keeper is None:
                    raise
                keeper.check()
            return

        latest = progress

        try:
            request_json(
                leader, "/v1/progress",
                {**progress, "run_id": job["run_id"], "lease_token": job["lease_token"]},
            )
        except HTTPError as error:
            if error.code == 409:
                raise LeaseLost("progress rejected for stale lease") from error
            raise
        except OSError:
            if keeper is None:
                raise
            keeper.check()

    try:
        assert process.stdout is not None and process.stderr is not None

        with selectors.DefaultSelector() as selector:
            for stream, kind in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)

            # A quiet stdout pipe never prevents a campaign stop from being observed.
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()

                if keeper is not None:
                    keeper.check()

                if now >= next_resource:
                    usage = process_group_usage(process.pid)
                    if usage is not None:
                        component = "coordinator" if job.get("reserved_workers") else "solver"
                        shard_index = -1 if component == "coordinator" else 0
                        try:
                            request_json(leader, "/v1/resource-usage", {
                                "run_id": job["run_id"], "lease_token": job["lease_token"],
                                "component": component, "shard_index": shard_index,
                                "cpu_microseconds": usage[0], "peak_rss_bytes": usage[1],
                            })
                        except HTTPError as error:
                            if error.code == 409:
                                raise LeaseLost("resource usage rejected for stale lease") from error
                            raise
                        except OSError:
                            if keeper is None:
                                raise
                            keeper.check()
                    next_resource = now + 10

                # Acknowledge only after the complete immutable image has been published.
                if pending_snapshot is not None and pending_snapshot.done():
                    pending_snapshot.result()
                    pending_snapshot = None

                    if process.poll() is None:
                        assert process.stdin is not None
                        process.stdin.write(b"\n")
                        process.stdin.flush()

                if process.poll() is None and now >= next_control:
                    control = keeper.control() if keeper is not None else request_json(
                        leader, "/v1/run-control",
                        {"run_id": job["run_id"], "lease_token": job["lease_token"]},
                    )
                    next_control = time.monotonic() + control_seconds

                    # Latch one intentional signal; wait for a valid solver boundary.
                    if control["stop_requested"] and not stopped:
                        stopped = True
                        stop_deadline = time.monotonic() + stop_grace_seconds

                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass

                # A bounded grace period leaves the previously committed checkpoint intact.
                if stopped and not forced and stop_deadline is not None:
                    if time.monotonic() >= stop_deadline and process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                            forced = True
                        except ProcessLookupError:
                            pass

                for key, _ in selector.select(timeout=min(control_seconds, 0.2)):
                    chunk = os.read(key.fileobj.fileno(), 65536)

                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue

                    if key.data == "stderr":
                        stderr_tail.extend(chunk)
                        del stderr_tail[:-65536]
                        continue

                    stdout_buffer.extend(chunk)

                    # Consume complete records; enforce a bounded partial-record buffer.
                    while b"\n" in stdout_buffer:
                        line, _, remainder = stdout_buffer.partition(b"\n")
                        stdout_buffer = bytearray(remainder)

                        if line:
                            forward_line(bytes(line))

                    if len(stdout_buffer) > 1024 * 1024:
                        raise ValueError("solver progress record exceeds 1 MiB")

            if stdout_buffer:
                forward_line(bytes(stdout_buffer))

        return {
            "return_code": process.wait(),
            "stopped": stopped,
            "forced": forced,
            "stderr": stderr_tail.decode("utf-8", errors="replace"),
            "progress": latest,
        }
    finally:
        cancelled.set()

        # Protocol errors and operator interrupts must not leave an orphaned solver.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)

        if process.stdin is not None:
            process.stdin.close()

        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

        if process.stdout is not None:
            process.stdout.close()

        if process.stderr is not None:
            process.stderr.close()


# Execute one fenced lease with immutable recovery and per-lease mutable state.
def run_job(
    leader: str,
    job: dict[str, Any],
    cpus: list[int],
    work_root: Path,
    storage_root: Path,
    storage_url: str,
    control_seconds: float = 1.0,
    stop_grace_seconds: float = 1800.0,
) -> None:
    """Restore, supervise, snapshot, and complete job; stale leases never submit results."""

    run_id = job["run_id"]
    token = job["lease_token"]
    identity = {"run_id": run_id, "lease_token": token}
    run_directory = work_root / run_id / token
    run_directory.mkdir(parents=True, exist_ok=False)
    output = run_directory / "result.bin"
    checkpoint = run_directory / "solver.checkpoint.json"
    maximum = int(job.get("max_checkpoint_bytes", DEFAULT_MAX_BYTES))
    keeper = LeaseKeeper(leader, job, control_seconds)
    failure_kind = SolverOutcome.ENGINE_FAILURE.value

    # Acknowledge snapshot capture only while this attempt still owns the calculation.
    def snapshot(cursor: int, cancelled: threading.Event) -> None:
        """Copy cursor's quiescent state, then retry publication within the live lease."""

        def check() -> None:
            """Abort bounded I/O promptly on lost ownership or supervisor shutdown."""

            keeper.check()

            if cancelled.is_set():
                raise RuntimeError("checkpoint capture cancelled")

        with storage_transaction(storage_root, check):
            try:
                manifest = capture_checkpoint(job["specification"], run_id, run_directory,
                                              storage_root, cursor, maximum, check)
            except OSError:
                # Reclaim only obsolete indexed bytes before one disk-admission retry.
                collect_garbage_locked(leader, job, storage_root)
                check()
                manifest = capture_checkpoint(job["specification"], run_id, run_directory,
                                              storage_root, cursor, maximum, check)

            while True:
                check()

                try:
                    request_json(leader, "/v1/checkpoint", {
                        **identity, "manifest": manifest, "manifest_hash": calculation_id(manifest),
                    })
                    return
                except HTTPError as error:
                    if error.code == 409:
                        raise LeaseLost("checkpoint rejected for stale lease") from error
                    raise
                except OSError:
                    cancelled.wait(min(control_seconds, 1.0))

    try:
        keeper.start()
        excluded: list[str] = []

        # Try the newest image first; retain older snapshots for damaged/missing-copy fallback.
        while True:
            keeper.check()

            if keeper.stopped:
                request_json(leader, "/v1/requeue", {
                    **identity, "reason": SolverOutcome.INTENTIONAL_STOP.value,
                })
                return

            candidate = request_json(leader, "/v1/recovery", {**identity, "exclude": excluded})
            task = candidate.get("checkpoint")

            if task is None:
                if candidate.get("has_checkpoints"):
                    raise RuntimeError("no valid retained checkpoint is currently recoverable")
                break

            try:
                reporter = bad_blob_reporter(leader, {"node_name": job.get("node_name"),
                                                      "session_id": job.get("session_id", "")}, task["sources"])
                with storage_transaction(storage_root, keeper.check):
                    restore_checkpoint(task, job["specification"], run_id, run_directory,
                                       storage_root, maximum, keeper.check, reporter)
                    request_json(leader, "/v1/restored", {**identity, "manifest_hash": task["manifest_hash"]})
            except (OSError, ValueError) as error:
                excluded.append(task["manifest_hash"])
                print(f"checkpoint {task['manifest_hash']} unavailable: {error}", file=sys.stderr, flush=True)
                continue

            print(f"restored {run_id}: {task['manifest']['done']} committed units", flush=True)
            break

        adapter = adapters.get(job["specification"])
        specification = adapter.worker_specification(job["specification"], cpus)
        solver = solver_command(specification, output, checkpoint, int(job.get("checkpoint_seconds", 1800)))
        solver.extend(adapter.prepare(specification, run_directory, job, leader))
        solver.extend(adapter.locality_args(specification, storage_root, storage_url, work_root))
        if adapter.checkpoint_handshake(specification):
            solver.append("--checkpoint-handshake")
        command = [sys.executable, str(Path(__file__).with_name("affinity_exec.py")),
                   "--parent-pid", str(os.getpid()), "--cpus", ",".join(str(cpu) for cpu in cpus), "--", *solver]
        restart_count = 0
        environment = None
        if job.get("gpu_index") is not None:
            # The leader fenced this device for the lease; solvers read KH_GPU_DEVICE.
            environment = dict(os.environ, KH_GPU_DEVICE=str(int(job["gpu_index"])))

        while True:
            result = supervise_solver(command, leader, job, control_seconds, stop_grace_seconds, keeper,
                                      snapshot, env=environment)
            return_code = result["return_code"]
            keeper.check()
            outcome = classify(return_code, result["stopped"], result["forced"],
                               adapter.retry_elsewhere(specification))

            if outcome is SolverOutcome.COMPLETE:
                break

            if outcome is SolverOutcome.INTENTIONAL_STOP:
                request_json(leader, "/v1/requeue", {
                    **identity, "reason": outcome.value,
                })
                return

            if outcome is SolverOutcome.STOP_FAILURE:
                failure_kind = outcome.value
                raise RuntimeError(f"solver failed while stopping (exit {return_code}): {result['stderr'].strip()}")

            if restart_count >= 1:
                raise RuntimeError(f"solver exited {return_code} after restart: {result['stderr'].strip()}")

            if adapter.retry_elsewhere(specification):
                if int(job.get("engine_failures", 0)) >= 1:
                    raise RuntimeError(f"distributed solver failed after retry: {result['stderr'].strip()}")
                request_json(leader, "/v1/requeue", {
                    **identity, "reason": outcome.value,
                })
                return
            restart_count += 1
            print(f"restarting solver for {run_id} from its checkpoint", file=sys.stderr, flush=True)

        adapter.validate_result(specification, output)
        with storage_transaction(storage_root, keeper.check):
            digest, path = store_blob(output, storage_root, keeper.check)
            keeper.check()
            request_json(leader, "/v1/complete", {
                **identity, "artifact_hash": digest, "artifact_size": path.stat().st_size,
                "artifact_location": f"{storage_url.rstrip('/')}/blobs/{digest}",
            })
        adapter.cleanup(specification, run_directory)

    except LeaseLost as error:
        print(f"abandoned stale lease {run_id}: {error}", file=sys.stderr, flush=True)
    except Exception as error:
        try:
            keeper.check()
            request_json(leader, "/v1/fail", {
                **identity, "error": str(error),
                "failure_kind": failure_kind,
            })
        except (OSError, LeaseLost):
            print(f"lease ended without a failure submission: {error}", file=sys.stderr, flush=True)
    finally:
        keeper.close()


# Build the long-running agent command interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the node-agent argument parser."""

    parser = argparse.ArgumentParser(
        description="Run the first-draft king_hamming node agent.",
        epilog="Example: ./agent.py run --leader http://merlin:8041 --name fearless --storage-url http://fearless:8042",
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="register and execute leased work")
    run.add_argument("--leader", default="http://127.0.0.1:8041")
    run.add_argument("--name", default=socket.gethostname())
    run.add_argument("--cpus", default="auto", help="auto or a Linux CPU list such as 0-3,6")
    run.add_argument("--slots", default="1",
                     help="1 for whole-host compatibility, auto for one process slot per CPU, or a count")
    run.add_argument("--storage-only", action="store_true", help="serve and replicate data without leasing calculations")
    run.add_argument("--leader-node", action="store_true", help="reserve one physical core")
    run.add_argument("--work-root", type=Path, default=Path("state/work"))
    run.add_argument("--storage-root", type=Path, default=Path("state/blobs"))
    run.add_argument("--storage-listen", default="0.0.0.0:8042", metavar="HOST:PORT")
    run.add_argument("--storage-url", default="http://127.0.0.1:8042")
    run.add_argument("--runtime-version", default="",
                     help="SHA-256 identity of the deployed runtime bundle")
    run.add_argument("--poll-seconds", type=float, default=2.0)
    run.add_argument("--control-seconds", type=float, default=1.0)
    run.add_argument("--stop-grace-seconds", type=float, default=1800.0)
    return parser


def register_node(leader: str, record: dict[str, Any], retry_seconds: float = 180) -> dict:
    """Retry transient startup failures with the same idempotent session identity."""
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            return request_json(leader, "/v1/register", record)
        except OSError as error:
            if (isinstance(error, HTTPError) and error.code < 500) or time.monotonic() >= deadline:
                raise
            print(f"registration delayed; retrying: {error}", file=sys.stderr, flush=True)
            time.sleep(1)


# Register, heartbeat, serve blobs, and execute fenced CPU-team leases.
def main() -> int:
    """Run the selected agent command and return an exit status."""

    parser = build_parser()
    arguments = parser.parse_args()

    if arguments.command is None:
        parser.print_help()
        return 0

    durations = (arguments.poll_seconds, arguments.control_seconds, arguments.stop_grace_seconds)

    if any(not math.isfinite(value) for value in durations) or \
            arguments.poll_seconds <= 0 or arguments.control_seconds <= 0 or arguments.stop_grace_seconds < 0:
        parser.error("poll/control intervals must be positive and stop grace nonnegative")

    cpus = (
        ordered_cpus(arguments.leader_node)
        if arguments.cpus == "auto"
        else parse_cpu_list(arguments.cpus)
    )

    if not cpus:
        parser.error("no CPUs remain after reservation")
    try:
        slot_count = len(cpus) if arguments.slots == "auto" else int(arguments.slots)
    except ValueError:
        parser.error("--slots must be auto or a positive integer")
    if not 1 <= slot_count <= len(cpus):
        parser.error("--slots must not exceed the assigned CPU count")
    slot_cpus = [cpus[index::slot_count] for index in range(slot_count)]

    physical_cores = set()
    for cpu in cpus:
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            package = int((topology / "physical_package_id").read_text())
            core = int((topology / "core_id").read_text())
        except (OSError, ValueError):
            physical_cores.clear()
            break
        physical_cores.add((package, core))

    arguments.storage_root.mkdir(parents=True, exist_ok=True)
    arguments.work_root.mkdir(parents=True, exist_ok=True)
    node_record = {
        "node_name": arguments.name,
        "address": arguments.storage_url,
        "cpu_set": ",".join(str(cpu) for cpu in cpus),
        "storage_root": str(arguments.storage_root.resolve()),
        "session_id": str(uuid.uuid4()),
        "storage_only": arguments.storage_only,
        "memory_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"),
        "storage_free_bytes": shutil.disk_usage(arguments.storage_root).free,
        "runtime_version": arguments.runtime_version,
        "physical_core_count": len(physical_cores),
        "storage_generation": storage_generation(arguments.storage_root),
        "slots": [{"slot_id": index, "cpu_set": ",".join(map(str, values))}
                  for index, values in enumerate(slot_cpus)],
        "gpus": [] if arguments.storage_only else gpus.detect(),
    }

    storage_host, storage_port = arguments.storage_listen.rsplit(":", 1)
    storage_server = ThreadingHTTPServer(
        (storage_host, int(storage_port)),
        make_storage_handler(arguments.storage_root, arguments.leader, node_record, cpus),
    )
    storage_thread = threading.Thread(target=storage_server.serve_forever, daemon=True)
    storage_thread.start()
    registration = register_node(arguments.leader, node_record)
    stop_event = threading.Event()
    heartbeat = threading.Thread(
        target=heartbeat_loop,
        args=(arguments.leader, node_record, stop_event, registration["heartbeat_seconds"]),
        daemon=True,
    )
    heartbeat.start()
    replication = threading.Thread(
        target=replication_loop,
        args=(arguments.leader, node_record, arguments.storage_root, stop_event, arguments.poll_seconds),
        daemon=True,
    )
    replication.start()
    print(f"agent {arguments.name} using CPUs {node_record['cpu_set']} in {slot_count} slots",
          flush=True)
    lease_gate = threading.Lock()
    next_idle_poll = [0.0]

    def lease_loop(slot_id: int, assigned: list[int]) -> None:
        """Poll and supervise the independent fenced lease owned by one CPU slot."""

        while not stop_event.is_set():
            if arguments.storage_only:
                stop_event.wait(arguments.poll_seconds)
                continue
            try:
                with lease_gate:
                    delay = next_idle_poll[0] - time.monotonic()
                    if delay > 0:
                        lease = None
                    else:
                        lease = request_json(
                            arguments.leader, "/v1/lease",
                            {"node_name": arguments.name,
                             "session_id": node_record["session_id"],
                             "slot_id": slot_id},
                        )
                        next_idle_poll[0] = (time.monotonic() if lease.get("job") is not None
                                             else time.monotonic() + arguments.poll_seconds)
            except HTTPError as error:
                if error.code == 409:
                    stop_event.set()
                    return
                print(f"slot {slot_id} lease error; retrying: {error}", file=sys.stderr, flush=True)
                stop_event.wait(arguments.poll_seconds)
                continue
            except OSError as error:
                print(f"leader unavailable; retrying: {error}", file=sys.stderr, flush=True)
                stop_event.wait(arguments.poll_seconds)
                continue
            if lease is None:
                stop_event.wait(min(arguments.poll_seconds, max(0.01, delay)))
                continue
            job = lease.get("job")
            if job is None:
                stop_event.wait(arguments.poll_seconds)
                continue
            actual = parse_cpu_list(job.get("assigned_cpu_set", ",".join(map(str, assigned))))
            run_job(arguments.leader, job, actual, arguments.work_root,
                    arguments.storage_root, arguments.storage_url,
                    arguments.control_seconds, arguments.stop_grace_seconds)

    try:
        workers = [threading.Thread(target=lease_loop, args=(index, values), daemon=True)
                   for index, values in enumerate(slot_cpus)]
        for worker in workers:
            worker.start()
        while not stop_event.wait(1):
            if not all(worker.is_alive() for worker in workers):
                raise RuntimeError("compute slot loop stopped unexpectedly")
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        storage_server.shutdown()
        storage_server.server_close()

    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
