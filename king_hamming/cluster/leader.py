#!/usr/bin/env python3
"""Run the king_hamming leader and its durable queue."""

from __future__ import annotations

import argparse
import json
import math
import os
import resource
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common import calculation_id, canonical_json
from blob_store import valid_digest
from checkpoints import DEFAULT_MAX_BYTES
import recovery
import retention
import replication
import adapters
import gpus
from resources import ResourceRequest, fits as resource_fits, normalized_slots

SCHEMA_VERSION = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    calculation_id TEXT NOT NULL,
    specification TEXT NOT NULL,
    state TEXT NOT NULL,
    priority INTEGER NOT NULL,
    from_scratch INTEGER NOT NULL,
    created REAL NOT NULL,
    started REAL,
    finished REAL,
    node_name TEXT,
    lease_token TEXT,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL DEFAULT '',
    artifact_hash TEXT,
    artifact_location TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS runs_queue
ON runs(state, priority DESC, created ASC);
CREATE TABLE IF NOT EXISTS resource_usage (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    lease_token TEXT NOT NULL,
    node_name TEXT NOT NULL,
    component TEXT NOT NULL,
    shard_index INTEGER NOT NULL,
    cpu_microseconds INTEGER NOT NULL,
    peak_rss_bytes INTEGER NOT NULL,
    recorded REAL NOT NULL,
    PRIMARY KEY(lease_token, component, shard_index)
);
CREATE INDEX IF NOT EXISTS resource_usage_run
ON resource_usage(run_id, recorded);
CREATE TABLE IF NOT EXISTS resource_usage_samples (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    lease_token TEXT NOT NULL,
    node_name TEXT NOT NULL,
    component TEXT NOT NULL,
    shard_index INTEGER NOT NULL,
    cpu_microseconds INTEGER NOT NULL,
    rss_bytes INTEGER NOT NULL,
    recorded REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_samples_identity
ON resource_usage_samples(lease_token,component,shard_index,recorded DESC);
CREATE INDEX IF NOT EXISTS resource_samples_recorded ON resource_usage_samples(recorded);
CREATE TABLE IF NOT EXISTS gpu_usage_samples (
    node_name TEXT NOT NULL,
    gpu_index INTEGER NOT NULL,
    util_percent INTEGER NOT NULL,
    memory_used_bytes INTEGER NOT NULL,
    recorded REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS gpu_usage_recorded ON gpu_usage_samples(recorded);
CREATE TABLE IF NOT EXISTS nodes (
    node_name TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    cpu_set TEXT NOT NULL,
    storage_root TEXT NOT NULL,
    last_heartbeat REAL NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_dispatch_pauses (
    node_name TEXT PRIMARY KEY REFERENCES nodes(node_name)
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_hash TEXT PRIMARY KEY,
    target_replicas INTEGER NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS replicas (
    artifact_hash TEXT NOT NULL REFERENCES artifacts(artifact_hash),
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    location TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY(artifact_hash, node_name)
);
"""


# Open one short-lived connection per request for straightforward thread safety.
def connect(database: Path, timeout: float = 30.0) -> sqlite3.Connection:
    """Open and configure a leader database connection."""

    connection = sqlite3.connect(database, timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    # In WAL mode NORMAL never corrupts the database; a power cut may only lose the
    # last few commits (recomputed tiles, renewed leases). FULL put an fsync, about
    # 7 ms and at times 500 ms on merlin's busy disk, inside every writer lock hold.
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


# sqlite3's own context manager only commits or rolls back; closing here keeps
# failed requests from holding file descriptors until garbage collection.
@contextmanager
def session(database: Path, timeout: float = 30.0):
    """Yield a connection whose transaction commits or rolls back, then close it."""

    connection = connect(database, timeout)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


@contextmanager
def writer_session(database: Path, route: str, health: "SchedulerHealth", timeout: float = 8.0):
    """Measure writer wait and transaction hold time, including commit/rollback."""

    started = time.monotonic()
    acquired = None
    error = None
    try:
        with session(database, timeout=timeout) as connection:
            connection.execute("BEGIN IMMEDIATE")
            acquired = time.monotonic()
            yield connection
    except Exception as failure:
        error = failure
        raise
    finally:
        ended = time.monotonic()
        health.transaction(route, (acquired or ended) - started,
                           0.0 if acquired is None else ended - acquired, error)


DEFAULT_DISK_FLOOR_BYTES = 10 * 1024**3


def disk_floor(connection) -> int:
    """Return the free-space floor in bytes (setting disk_floor_bytes; 0 turns the rule off)."""

    return int(retention.number(connection, "disk_floor_bytes", DEFAULT_DISK_FLOOR_BYTES))


SAMPLE_PRUNE_SECONDS = 60.0
SAMPLE_PRUNED = [0.0]


def push_copy(connection, digest: str, now: float, lease_seconds: float) -> None:
    """Reserve digest's next needed copy for a chosen node (see replication.push_next_copy)."""

    grace = max(lease_seconds, retention.number(
        connection, "replica_grace_seconds", retention.DEFAULT_REPLICA_GRACE_SECONDS))
    replication.push_next_copy(connection, digest, now, lease_seconds, grace, disk_floor(connection))


def low_disk(node, floor: int) -> bool:
    """Whether node reported less free space than floor. A negative figure means it never reported."""

    free = node["storage_free_bytes"]
    return floor > 0 and free is not None and 0 <= free < floor


def reconstruction_drain_target(connection, now, lease_seconds, resources):
    """Choose one suitable host to drain instead of idling the whole fleet."""

    nodes = connection.execute(
        "SELECT n.*,COUNT(active.run_id) AS active_count,"
        "COALESCE(SUM(active.exclusive_host),0) AS exclusive_count "
        "FROM nodes n LEFT JOIN runs active ON active.node_name=n.node_name "
        "AND active.state='running' WHERE n.compute_enabled=1 "
        "AND NOT EXISTS (SELECT 1 FROM node_dispatch_pauses pause WHERE pause.node_name=n.node_name) "
        "AND n.last_heartbeat>? AND NOT EXISTS "
        "(SELECT 1 FROM node_reservations reserve WHERE reserve.node_name=n.node_name) "
        "GROUP BY n.node_name ORDER BY active_count,n.node_name",
        (now-lease_seconds,),
    ).fetchall()
    for node in nodes:
        if node["exclusive_count"] or not resource_fits(node, resources, "coordinator"):
            continue
        if (int(node["memory_bytes"]) and
                resources.coordinator_memory_bytes > int(node["memory_bytes"]) - 2 * 1024**3):
            continue
        return node["node_name"]
    return None


# Locality is only a tie-break: priority, reconstruction and per-root fairness decide first.
def locality_order(connection, node_name, candidates, now):
    """Reorder candidates so node_name prefers runs whose inputs it already holds.

    The sort key is the SQL queue order with the adapter's locality score inserted
    just before creation time, so it can only separate runs of the same priority,
    phase, root load and estimate. KH_ROW_AFFINITY=0 restores the plain order.
    """

    if os.environ.get("KH_ROW_AFFINITY", "1") == "0" or len(candidates) < 2:
        return candidates
    groups: dict[int, tuple[Any, list]] = {}
    for candidate in candidates:
        specification = json.loads(candidate["specification"])
        adapter = adapters.get(specification)
        groups.setdefault(id(adapter), (adapter, []))[1].append(
            (candidate["run_id"], specification, candidate["created"]))
    scores: dict[str, int] = {}
    for adapter, items in groups.values():
        scores.update(adapter.locality_scores(connection, node_name, items, now))
    if not any(scores.values()):
        return candidates
    return sorted(candidates, key=lambda c: (
        c["progress_phase"] != "reconstructing", -c["priority"], c["peers"],
        -1.0 if c["estimated_seconds"] is None else c["estimated_seconds"],
        -scores.get(c["run_id"], 0), c["created"], c["run_id"]))


# Keep scheduler liveness separate from campaign policy and worker heartbeats.
class SchedulerHealth:
    """Record whether the background queue advance loop is making progress."""

    def __init__(self, stall_seconds: float) -> None:
        self.lock = threading.Lock()
        self.stall_seconds = stall_seconds
        self.last_attempt: float | None = None
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        self.transactions: dict[str, dict[str, float | int]] = {}
        self.last_lock_error: float | None = None

    def transaction(self, route: str, wait: float, held: float, error: BaseException | None) -> None:
        """Keep bounded per-route writer timing without printing every busy request."""

        with self.lock:
            if route not in self.transactions and len(self.transactions) >= 64:
                route = "other"
            item = self.transactions.setdefault(route, {"calls": 0, "wait_seconds": 0.0,
                                                        "held_seconds": 0.0, "max_wait_seconds": 0.0,
                                                        "max_held_seconds": 0.0, "lock_errors": 0})
            item["calls"] += 1
            item["wait_seconds"] += wait
            item["held_seconds"] += held
            item["max_wait_seconds"] = max(item["max_wait_seconds"], wait)
            item["max_held_seconds"] = max(item["max_held_seconds"], held)
            if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
                item["lock_errors"] += 1
                if route != "scheduler":
                    self.last_lock_error = time.time()

    def attempting(self) -> None:
        """Record the start of one scheduler transaction."""

        with self.lock:
            self.last_attempt = time.time()

    def succeeded(self) -> None:
        """Record a successful scheduler transaction and clear transient failure state."""

        with self.lock:
            self.last_success = time.time()
            self.last_error = None
            self.consecutive_failures = 0

    def failed(self, error: BaseException) -> None:
        """Record a failed attempt while allowing the scheduler loop to retry."""

        with self.lock:
            self.last_error = f"{type(error).__name__}: {error}"
            self.consecutive_failures += 1

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe current health description."""

        with self.lock:
            now = time.time()
            unfinished = (
                self.last_attempt is not None
                and (self.last_success is None or self.last_attempt > self.last_success)
            )
            stalled = bool(unfinished and now - self.last_attempt > self.stall_seconds)
            contended = self.last_lock_error is not None and now - self.last_lock_error < 60
            healthy = self.consecutive_failures == 0 and not stalled and not contended
            return {
                "status": "healthy" if healthy else "stalled" if stalled else "contended" if contended else "retrying",
                "healthy": healthy,
                "last_attempt": self.last_attempt,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "consecutive_failures": self.consecutive_failures,
                "last_lock_error": self.last_lock_error,
                "transactions": {route: dict(item) for route, item in self.transactions.items()},
            }


def annotate_idle_reasons(connection: sqlite3.Connection, nodes: list[dict[str, Any]],
                          runs: list[dict[str, Any]], campaign: str,
                          lease_seconds: float, now: float) -> None:
    """Explain each idle node using current queue, resource, and dependency state."""
    active = {run["node_name"] for run in runs
              if run["state"] == "running" and run.get("node_name")}
    reserved = {node["node_name"] for node in nodes if node.get("reserved_for")}
    free = [node for node in nodes if node.get("compute_enabled", 1) and
            node["node_name"] not in active | reserved and
            now - node["last_heartbeat"] <= lease_seconds]
    queued = connection.execute(
        "SELECT specification FROM runs WHERE state='queued' "
        "ORDER BY priority DESC,estimated_seconds,created LIMIT 100").fetchall()
    waiting = any(run["state"] == "waiting" for run in runs)

    for node in nodes:
        if now - node["last_heartbeat"] > lease_seconds:
            node["idle_reason"] = "unavailable"
        elif not node.get("compute_enabled", 1):
            node["idle_reason"] = "intentionally reserved/storage-only"
        elif node["node_name"] in active:
            node["idle_reason"] = "running"
        elif node["node_name"] in reserved:
            node["idle_reason"] = "matching partner reservation"
        elif campaign != "running":
            node["idle_reason"] = "campaign dispatch stopped"
        elif low_disk(node, disk_floor(connection)):
            node["idle_reason"] = f"low disk ({node['storage_free_bytes'] / 1024**3:.1f} GiB free)"
        elif connection.execute(
                "SELECT 1 FROM node_revalidation WHERE node_name=? LIMIT 1",
                (node["node_name"],)).fetchone() is not None:
            node["idle_reason"] = "validating retained storage"
        elif not queued:
            node["idle_reason"] = ("no dependency-ready tile or artifact replicas"
                                   if waiting else "queue empty")
        else:
            coordinator_fit = False
            partners_short = False
            for candidate in queued:
                specification = json.loads(candidate["specification"])
                adapter = adapters.get(specification)
                requirement = ResourceRequest.from_adapter(
                    adapter.resource_requirements(specification))
                if not resource_fits(node, requirement, "coordinator"):
                    continue
                coordinator_fit = True
                required = adapter.required_nodes(specification)
                partners = sum(other["node_name"] != node["node_name"] and
                               resource_fits(other, requirement, "worker")
                               for other in free)
                if partners >= required - 1:
                    node["idle_reason"] = "awaiting scheduler handoff"
                    break
                partners_short = partners_short or required > 1
            else:
                node["idle_reason"] = ("waiting for matching partners" if partners_short
                                       else "memory/CPU admission" if not coordinator_fit
                                       else "resource admission")


# Initialize schema and the stopped-generation setting.
def initialize(
    database: Path, checkpoint_seconds: int, lease_seconds: float = 60.0,
    max_checkpoint_bytes: int = DEFAULT_MAX_BYTES, checkpoint_keep: int = 3,
    visits_per_second: float = 100_000_000.0,
) -> None:
    """Create the leader database and store leader-controlled settings."""

    if checkpoint_seconds < 0 or not math.isfinite(lease_seconds) or lease_seconds <= 0 or max_checkpoint_bytes <= 0:
        raise ValueError("checkpoint interval/byte limit and lease duration are invalid")

    database.parent.mkdir(parents=True, exist_ok=True)

    with session(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(SCHEMA)

        # Add supervision fields without replacing existing runs or artifacts.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)")}
        additions = {
            "stop_requested": "INTEGER NOT NULL DEFAULT 0",
            "last_solver_heartbeat": "REAL",
            "last_progress_at": "REAL",
            "last_checkpoint_at": "REAL",
            "progress_checkpoint_done": "INTEGER NOT NULL DEFAULT 0",
            "progress_phase": "TEXT NOT NULL DEFAULT 'starting'",
            "progress_units": "TEXT NOT NULL DEFAULT 'steps'",
            "progress_details": "TEXT NOT NULL DEFAULT '{}'",
            "parent_run_id": "TEXT REFERENCES runs(run_id)",
            "control_state": "TEXT NOT NULL DEFAULT 'running'",
            "failure_kind": "TEXT",
            "slot_id": "INTEGER",
            "assigned_cpu_set": "TEXT",
            "reserved_memory_bytes": "INTEGER NOT NULL DEFAULT 0",
            "exclusive_host": "INTEGER NOT NULL DEFAULT 1",
            "gpu_index": "INTEGER",
        }

        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
        node_columns = {row["name"] for row in connection.execute("PRAGMA table_info(nodes)")}
        if "storage_free_bytes" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN storage_free_bytes INTEGER NOT NULL DEFAULT 0")
        if "gpus_json" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN gpus_json TEXT NOT NULL DEFAULT '[]'")
        if "private_address" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN private_address TEXT NOT NULL DEFAULT ''")
        if "private_group" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN private_group TEXT NOT NULL DEFAULT ''")
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('campaign_state', 'running')"
        )
        connection.execute(
            "INSERT INTO settings(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),),
        )
        connection.execute(
            "INSERT INTO settings(key, value) VALUES('checkpoint_seconds', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(checkpoint_seconds),),
        )

        recovery.initialize(connection, lease_seconds, max_checkpoint_bytes)
        retention.initialize(connection, checkpoint_keep)
        replication.initialize(connection)
        connection.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('disk_floor_bytes',?)",
                           (str(DEFAULT_DISK_FLOOR_BYTES),))
        adapters.initialize(connection)
        if not math.isfinite(visits_per_second) or visits_per_second <= 0:
            raise ValueError("visit rate must be finite and positive")
        connection.execute(
            "INSERT INTO settings(key,value) VALUES('visits_per_second',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(visits_per_second),),
        )
        if "estimated_seconds" not in columns:
            connection.execute("ALTER TABLE runs ADD COLUMN estimated_seconds REAL NOT NULL DEFAULT 1e100")
        for run in connection.execute("SELECT run_id,specification FROM runs").fetchall():
            try:
                estimate = adapters.estimate_seconds(json.loads(run["specification"]), visits_per_second)
            except (ValueError, KeyError, TypeError):
                estimate = 1e100
            connection.execute("UPDATE runs SET estimated_seconds=? WHERE run_id=?", (estimate, run["run_id"]))
        connection.execute("CREATE INDEX IF NOT EXISTS runs_runtime_queue ON runs(state,priority DESC,estimated_seconds,created,run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS runs_parent_active ON runs(parent_run_id,state)")
        connection.execute("CREATE INDEX IF NOT EXISTS lease_retry_nodes ON lease_history(run_id,node_name,outcome,finished)")


# Decode a bounded JSON request body.
def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """Read and validate a JSON object from an HTTP request."""

    length = int(handler.headers.get("Content-Length", "0"))

    if not 0 <= length <= 1024 * 1024:
        raise ValueError("request body exceeds 1 MiB")

    value = json.loads(handler.rfile.read(length) or b"{}")

    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")

    return value


# Build a request handler bound to one database path.
def make_handler(
    database: Path, scheduler: SchedulerHealth | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Return an HTTP handler class using database."""

    scheduler = scheduler or SchedulerHealth(30.0)

    class Handler(BaseHTTPRequestHandler):
        """Serve the versioned leader protocol."""

        server_version = "king-hamming-first/0.1"

        # Write a compact JSON response.
        def send_json(self, status: HTTPStatus, value: Any) -> None:
            """Send value as a JSON response."""

            body = canonical_json(value) + b"\n"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # Dispatch read-only protocol requests.
        def do_GET(self) -> None:
            """Handle status and health requests."""

            route = urlparse(self.path).path

            if route == "/v1/health":
                scheduler_status = scheduler.snapshot()
                self.send_json(
                    HTTPStatus.OK if scheduler_status["healthy"] else HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": scheduler_status["healthy"], "scheduler": scheduler_status},
                )
                return

            if route == "/v1/status":
                with session(database) as connection:
                    campaign = connection.execute(
                        "SELECT value FROM settings WHERE key='campaign_state'"
                    ).fetchone()[0]
                    checkpoint_seconds = int(connection.execute(
                        "SELECT value FROM settings WHERE key='checkpoint_seconds'"
                    ).fetchone()[0])
                    runs = [dict(row) for row in connection.execute(
                        "SELECT * FROM runs WHERE state='running' OR run_id IN "
                        "(SELECT run_id FROM runs WHERE parent_run_id IS NULL "
                        "ORDER BY created DESC LIMIT 50) "
                        "ORDER BY (state='running') DESC,created DESC"
                    )]
                    nodes = [dict(row) for row in connection.execute(
                        "SELECT * FROM nodes ORDER BY node_name"
                    )]
                    reservations = {entry["node_name"]: entry["run_id"] for entry in connection.execute(
                        "SELECT node_name,run_id FROM node_reservations"
                    )}
                    dispatch_pauses = {entry[0] for entry in connection.execute(
                        "SELECT node_name FROM node_dispatch_pauses")}
                    for participant in nodes:
                        participant["reserved_for"] = reservations.get(participant["node_name"])
                        participant["dispatch_paused"] = participant["node_name"] in dispatch_pauses
                    recovery.add_status(connection, runs, time.time())
                    adapters.augment_status(connection, runs, time.time())
                    run_ids = [run["run_id"] for run in runs]
                    placeholders = ",".join("?" for _ in run_ids)
                    checkpoint_counts = {}
                    usage_by_run = {run_id: [] for run_id in run_ids}
                    if run_ids:
                        checkpoint_counts = {row["run_id"]: row for row in connection.execute(
                            f"SELECT run_id,COUNT(*) AS total,SUM(retired_at IS NULL) AS retained "
                            f"FROM checkpoints WHERE run_id IN ({placeholders}) GROUP BY run_id", run_ids,
                        )}
                        for item in connection.execute(
                            f"SELECT u.run_id,u.lease_token,u.node_name,u.component,u.shard_index,"
                            f"u.cpu_microseconds,u.peak_rss_bytes,u.recorded,h.attempt "
                            f"FROM resource_usage u LEFT JOIN lease_history h USING(lease_token) "
                            f"WHERE u.run_id IN ({placeholders}) "
                            f"ORDER BY u.run_id,COALESCE(h.attempt,0),u.shard_index,u.component", run_ids,
                        ):
                            usage = dict(item)
                            samples = connection.execute(
                                "SELECT cpu_microseconds,recorded FROM resource_usage_samples "
                                "WHERE lease_token=? AND component=? AND shard_index=? "
                                "ORDER BY recorded DESC LIMIT 2",
                                (item["lease_token"], item["component"], item["shard_index"]),
                            ).fetchall()
                            if len(samples) == 2 and samples[0]["recorded"] > samples[1]["recorded"]:
                                elapsed = samples[0]["recorded"] - samples[1]["recorded"]
                                cpu = max(0, samples[0]["cpu_microseconds"] -
                                          samples[1]["cpu_microseconds"]) / 1_000_000
                                cpu_set = next((run.get("assigned_cpu_set") or ""
                                                for run in runs
                                                if run["run_id"] == item["run_id"] and
                                                run["node_name"] == item["node_name"]), "")
                                if not cpu_set:
                                    cpu_set = next((node["cpu_set"] for node in nodes
                                                    if node["node_name"] == item["node_name"]), "")
                                assigned = max(1, len([value for value in cpu_set.split(",") if value]))
                                usage["assigned_cpu_utilization"] = min(1.0, cpu / elapsed / assigned)
                                usage["sample_seconds"] = elapsed
                            usage_by_run[item["run_id"]].append(usage)
                    for run in runs:
                        counts = checkpoint_counts.get(run["run_id"])
                        run["retained_checkpoints"] = 0 if counts is None else counts["retained"] or 0
                        run["retired_checkpoints"] = (0 if counts is None else
                            counts["total"] - run["retained_checkpoints"])
                        run["resource_usage"] = usage_by_run[run["run_id"]]
                    lease_seconds = recovery.setting(connection, "lease_seconds")
                    checkpoint_keep = int(recovery.setting(connection, "checkpoint_keep"))
                    schema_version = int(connection.execute(
                        "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0])
                    artifacts = [dict(row) for row in connection.execute(
                        "WITH root_artifacts AS (SELECT DISTINCT artifact_hash FROM runs "
                        "WHERE parent_run_id IS NULL AND artifact_hash IS NOT NULL) "
                        "SELECT a.artifact_hash,a.target_replicas,"
                        "SUM(CASE WHEN n.last_heartbeat>? THEN 1 ELSE 0 END) AS replicas,"
                        "COUNT(r.node_name) AS indexed_replicas FROM root_artifacts root "
                        "JOIN artifacts a USING(artifact_hash) LEFT JOIN replicas r USING(artifact_hash) "
                        "LEFT JOIN nodes n USING(node_name) GROUP BY a.artifact_hash "
                        "ORDER BY a.created DESC LIMIT 200", (time.time()-lease_seconds,),
                    )]
                    annotate_idle_reasons(connection, nodes, runs, campaign,
                                          lease_seconds, time.time())

                # Keep deliberate stopping, solver liveness, and stalled work distinct.
                now = time.time()

                for run in runs:
                    if run["state"] != "running":
                        run["solver_health"] = run["state"]
                    elif run["stop_requested"]:
                        run["solver_health"] = "stopping"
                    elif run["last_solver_heartbeat"] is None:
                        run["solver_health"] = (
                            "heartbeat-missing" if run["started"] is not None and now - run["started"] > 30
                            else "starting"
                        )
                    elif now - run["last_solver_heartbeat"] > 30:
                        run["solver_health"] = "heartbeat-missing"
                    elif run["last_progress_at"] is not None and now - run["last_progress_at"] >= 1800:
                        run["solver_health"] = "stalled"
                    elif run["last_progress_at"] is not None and now - run["last_progress_at"] >= 300:
                        run["solver_health"] = "no-progress-warning"
                    else:
                        run["solver_health"] = "responding"

                # Derive unavailability without trusting a stale persisted label.
                for node in nodes:
                    if time.time() - node["last_heartbeat"] > lease_seconds:
                        node["state"] = "unavailable"

                self.send_json(
                    HTTPStatus.OK,
                    {
                        "campaign_state": campaign,
                        "checkpoint_seconds": checkpoint_seconds,
                        "lease_seconds": lease_seconds,
                        "checkpoint_keep": checkpoint_keep,
                        "schema_version": schema_version,
                        "capabilities": ["known-capacity-admission-v1", "partitioned-matching-v1",
                                         "gpu-leases-v1"],
                        "scheduler": scheduler.snapshot(),
                        "runs": runs,
                        "nodes": nodes,
                        "artifacts": artifacts,
                    },
                )
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})

        # Dispatch state-changing protocol requests.
        def do_POST(self) -> None:
            """Handle queue, lease, heartbeat, and completion requests."""

            route = urlparse(self.path).path

            try:
                request = read_json(self)
                response = self.dispatch_post(route, request)
                self.send_json(HTTPStatus.OK, response)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except PermissionError as error:
                self.send_json(HTTPStatus.CONFLICT, {"error": str(error)})
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower():
                    raise
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "leader database busy; retry"})

        # Apply one protocol operation transactionally.
        def dispatch_post(self, route: str, request: dict[str, Any]) -> dict[str, Any]:
            """Apply route using request and return its response object."""

            if route == "/v1/tile-input" and "publish_bands" not in request:
                # Immutable input descriptors do not need the global writer lock.
                with session(database) as connection:
                    connection.execute("BEGIN")
                    now = time.time()
                    row = connection.execute("SELECT * FROM runs WHERE run_id=?",
                                             (request["run_id"],)).fetchone()
                    if (row is None or row["lease_token"] != request["lease_token"] or
                            row["state"] != "running" or row["lease_expires"] is None or
                            row["lease_expires"] <= now):
                        raise PermissionError("stale or invalid lease")
                    specification = json.loads(row["specification"])
                    adapter = adapters.get(specification)
                    if route not in adapter.input_routes:
                        raise ValueError("solver has no tile inputs")
                    return adapter.inputs(connection, row, request, now)

            if route == "/v1/revalidation-batch":
                # Almost every poll finds nothing pending; that needs no writer lock.
                with session(database) as connection:
                    node = recovery.require_node(connection, request)
                    if connection.execute("SELECT 1 FROM node_revalidation WHERE node_name=? LIMIT 1",
                                          (node["node_name"],)).fetchone() is None:
                        return {"mode": node["storage_validation_mode"], "records": []}

            if route == "/v1/replication":
                # Searching tens of thousands of tile artifacts must not hold
                # the one SQLite writer lock needed by leases and heartbeats.
                with session(database) as connection:
                    now = time.time()
                    node = recovery.require_node(connection, request)
                    inventory = recovery.revalidation(connection, node["node_name"], now)
                    if inventory is not None:
                        return {"replication": inventory}
                    floor = disk_floor(connection)
                    if low_disk(node, floor):
                        return {"replication": None}
                    assigned = replication.pending_assignment(
                        connection, node, now, recovery.setting(connection, "lease_seconds"))
                    if assigned is not None:
                        return {"replication": assigned}
                    checkpoint = recovery.replication(connection, node["node_name"], now)
                    if checkpoint is not None:
                        return {"replication": checkpoint}
                    if not replication.scan_allowed(node["node_name"], now):
                        return {"replication": None}
                    grace = max(recovery.setting(connection, "lease_seconds"), retention.number(
                        connection, "replica_grace_seconds", retention.DEFAULT_REPLICA_GRACE_SECONDS))
                    candidate = replication.select_candidate(
                        connection, node, now, recovery.setting(connection, "lease_seconds"),
                        grace, floor)
                if candidate is None:
                    replication.scan_found_nothing(node["node_name"], now)
                    return {"replication": None}
                with writer_session(database, route, scheduler) as connection:
                    now = time.time()
                    node = recovery.require_node(connection, request)
                    floor = disk_floor(connection)
                    if low_disk(node, floor):
                        return {"replication": None}
                    lease = recovery.setting(connection, "lease_seconds")
                    grace = max(lease, retention.number(
                        connection, "replica_grace_seconds", retention.DEFAULT_REPLICA_GRACE_SECONDS))
                    return {"replication": replication.reserve_candidate(
                        connection, node, candidate, now, lease, grace, floor)}

            with writer_session(database, route, scheduler) as connection:
                # Serialize lease validation and mutation to fence racing stale submissions.
                now = time.time()
                # The scheduler expires leases once per tick. Doing a global
                # expiry scan on every heartbeat or blob request held the writer
                # lock in proportion to unrelated work.
                # A lease request also expires promptly so an idle replacement
                # need not wait for the next scheduler tick.
                if route == "/v1/lease":
                    recovery.expire(connection, now)
                lease_seconds = recovery.setting(connection, "lease_seconds")
                if route == "/v1/enqueue":
                    specification = request["specification"]

                    if not isinstance(specification, dict):
                        raise TypeError("specification must be an object")

                    estimated_seconds = adapters.estimate_seconds(specification, recovery.setting(connection, "visits_per_second"))
                    adapter = adapters.get(specification)
                    adapter.validate(specification, internal=False)
                    identity = calculation_id(specification)
                    rerun = bool(request.get("rerun", False))
                    from_scratch = bool(request.get("from_scratch", False))

                    # Normal duplicates reuse the newest existing run.
                    if not rerun and not from_scratch:
                        row = connection.execute(
                            "SELECT run_id, state FROM runs WHERE calculation_id=? "
                            "ORDER BY created DESC LIMIT 1",
                            (identity,),
                        ).fetchone()

                        if row is not None:
                            return {"run_id": row["run_id"], "state": row["state"], "reused": True}

                    run_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO runs(run_id, calculation_id, specification, state, "
                        "priority, from_scratch, created, estimated_seconds) VALUES(?, ?, ?, 'queued', ?, ?, ?, ?)",
                        (
                            run_id,
                            identity,
                            canonical_json(specification).decode("utf-8"),
                            int(request.get("priority", 0)),
                            int(from_scratch),
                            now,
                            estimated_seconds,
                        ),
                    )
                    state = adapter.enqueue(connection, run_id, specification, now)
                    return {"run_id": run_id, "state": state, "reused": False}

                if route == "/v1/control":
                    state = request["state"]

                    if state not in {"running", "stopped"}:
                        raise ValueError("state must be running or stopped")

                    connection.execute(
                        "UPDATE settings SET value=? WHERE key='campaign_state'",
                        (state,),
                    )

                    # Latch stops until the affected lease has actually quiesced.
                    if state == "stopped":
                        connection.execute("UPDATE runs SET stop_requested=1 WHERE state='running'")

                    return {"campaign_state": state}

                if route == "/v1/node-control":
                    name, action = request["node_name"], request["action"]
                    if type(name) is not str or action not in {"drain", "resume"}:
                        raise ValueError("node-control requires a node name and drain/resume action")
                    if connection.execute("SELECT 1 FROM nodes WHERE node_name=?", (name,)).fetchone() is None:
                        raise ValueError("unknown node")
                    if action == "drain":
                        connection.execute("INSERT OR IGNORE INTO node_dispatch_pauses(node_name) VALUES(?)", (name,))
                    else:
                        connection.execute("DELETE FROM node_dispatch_pauses WHERE node_name=?", (name,))
                    return {"node_name": name, "dispatch_paused": action == "drain"}

                if route == "/v1/run-command":
                    run_id = request["run_id"]
                    action = request["action"]
                    row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
                    if row is None:
                        raise ValueError("unknown run")
                    if action == "reprioritize":
                        priority = request.get("priority")
                        if type(priority) is not int:
                            raise ValueError("priority must be an integer")
                        connection.execute("UPDATE runs SET priority=? WHERE run_id=?", (priority, run_id))
                        return {"run_id": run_id, "state": row["state"], "priority": priority}
                    if action == "retry-reconstruction":
                        specification = json.loads(row["specification"])
                        return adapters.get(specification).retry_reconstruction(
                            connection, row, specification, now)
                    if action not in {"cancel", "pause", "resume"}:
                        raise ValueError("action must be cancel, pause, resume, reprioritize, or retry-reconstruction")
                    if action == "resume":
                        if row["state"] != "paused":
                            raise ValueError("only a paused run can be resumed")
                        specification = json.loads(row["specification"])
                        state, phase = adapters.get(specification).resume_transition(
                            connection, row, specification, now,
                        )
                        if state not in {"queued", "waiting"} or not isinstance(phase, str):
                            raise ValueError("adapter returned invalid resume transition")
                        connection.execute(
                            "UPDATE runs SET state=?, control_state='running', stop_requested=0, "
                            "progress_phase=?, progress_message='resumed' WHERE run_id=?",
                            (state, phase, run_id),
                        )
                        return {"run_id": run_id, "state": state}
                    target = "cancelled" if action == "cancel" else "paused"
                    if row["state"] in {"complete", "failed", "cancelled"}:
                        raise ValueError("terminal runs cannot be controlled")
                    if row["state"] == "running":
                        connection.execute(
                            "UPDATE runs SET control_state=?,stop_requested=1,progress_message=? WHERE run_id=?",
                            (target, f"{action} requested", run_id),
                        )
                        return {"run_id": run_id, "state": "stopping", "target_state": target}
                    connection.execute(
                        "UPDATE runs SET state=?,control_state=?,finished=?,progress_phase=?,progress_message=? WHERE run_id=?",
                        (target, target, now if target == "cancelled" else None, target, target, run_id),
                    )
                    return {"run_id": run_id, "state": target}

                if route in {"/v1/register", "/v1/heartbeat"}:
                    existing = connection.execute(
                        "SELECT * FROM nodes WHERE node_name=?", (request["node_name"],),
                    ).fetchone()
                    session_id = str(request.get("session_id", ""))
                    storage_generation = str(request.get("storage_generation", ""))
                    if storage_generation:
                        try:
                            uuid.UUID(storage_generation)
                        except ValueError as error:
                            raise ValueError("invalid storage generation") from error
                    same_storage = bool(existing is not None and storage_generation and
                                        existing["storage_generation"] == storage_generation)

                    if route == "/v1/heartbeat":
                        recovery.require_node(connection, request)
                    elif existing is not None and existing["session_id"] != session_id:
                        # A restarted agent cannot inherit an unacknowledged old computation lease.
                        for active in connection.execute(
                            "SELECT * FROM runs WHERE node_name=? AND state='running'", (request["node_name"],),
                        ).fetchall():
                            recovery.retire(connection, active, now, "agent incarnation changed")

                        # A restarted reserved partner invalidates the whole group lease.
                        for active in connection.execute(
                            "SELECT r.* FROM runs r JOIN node_reservations reserve ON reserve.run_id=r.run_id "
                            "WHERE reserve.node_name=? AND r.state='running'", (request["node_name"],),
                        ).fetchall():
                            recovery.retire(connection, active, now, "reserved partner incarnation changed")

                        # Revalidate storage after a restart; disk contents may have disappeared.
                        connection.execute(
                            "INSERT OR IGNORE INTO node_revalidation(node_name,kind,digest,created) SELECT node_name,'artifact',artifact_hash,? FROM replicas WHERE node_name=?",
                            (now, request["node_name"]),
                        )
                        connection.execute(
                            "INSERT OR IGNORE INTO node_revalidation(node_name,kind,digest,created) SELECT node_name,'checkpoint',manifest_hash,? FROM checkpoint_replicas WHERE node_name=?",
                            (now, request["node_name"]),
                        )
                        connection.execute("DELETE FROM replicas WHERE node_name=?", (request["node_name"],))
                        connection.execute("DELETE FROM checkpoint_replicas WHERE node_name=?", (request["node_name"],))

                    validation_mode = (existing["storage_validation_mode"]
                                       if route == "/v1/heartbeat" and existing is not None
                                       else "metadata" if same_storage else "hash"
                                       if existing is not None and existing["session_id"] != session_id
                                       else "verified")
                    if validation_mode != "verified" and connection.execute(
                            "SELECT 1 FROM node_revalidation WHERE node_name=? LIMIT 1",
                            (request["node_name"],)).fetchone() is None:
                        validation_mode = "verified"
                    gpu_list = (gpus.normalized(request.get("gpus")) if route == "/v1/register"
                                else gpus.from_record(existing) if existing is not None else [])
                    slots = normalized_slots(
                        request.get("slots") if route == "/v1/register" else
                        json.loads(existing["slots_json"] or "[]") if existing is not None else None,
                        request.get("cpu_set", existing["cpu_set"] if existing is not None else ""),
                    )
                    connection.execute(
                        "INSERT INTO nodes(node_name,address,private_address,private_group,cpu_set,storage_root,last_heartbeat,state,session_id,compute_enabled,memory_bytes,runtime_version,physical_core_count,storage_generation,storage_validation_mode,slots_json,storage_free_bytes,gpus_json) "
                        "VALUES(?,?,?,?,?,?,?,'healthy',?,?,?,?,?,?,?,?,?,?) ON CONFLICT(node_name) DO UPDATE SET "
                        "address=excluded.address,private_address=excluded.private_address,"
                        "private_group=excluded.private_group,cpu_set=excluded.cpu_set,storage_root=excluded.storage_root, "
                        "last_heartbeat=excluded.last_heartbeat,state='healthy',session_id=excluded.session_id, "
                        "compute_enabled=excluded.compute_enabled,memory_bytes=excluded.memory_bytes,"
                        "runtime_version=excluded.runtime_version,physical_core_count=excluded.physical_core_count,"
                        "storage_generation=excluded.storage_generation,storage_validation_mode=excluded.storage_validation_mode,"
                        "slots_json=excluded.slots_json,storage_free_bytes=excluded.storage_free_bytes,"
                        "gpus_json=excluded.gpus_json",
                        (request["node_name"], request.get("address", ""),
                         request.get("private_address", ""), request.get("private_group", ""),
                         request.get("cpu_set", ""),
                         request.get("storage_root", ""), now, session_id,
                         int(not request.get("storage_only", False)),
                         max(0, int(request.get(
                             "memory_bytes", existing["memory_bytes"] if existing is not None else 0))),
                         str(request.get(
                             "runtime_version", existing["runtime_version"] if existing is not None else ""))[:128],
                         max(0, int(request.get(
                             "physical_core_count",
                             existing["physical_core_count"] if existing is not None else 0))),
                         storage_generation, validation_mode, json.dumps(slots, separators=(",", ":")),
                         # -1: this agent does not report free space (so no floor applies), unlike 0 = full.
                         max(0, int(request["storage_free_bytes"])) if "storage_free_bytes" in request else -1,
                         json.dumps(gpu_list, separators=(",", ":"))),
                    )
                    if route == "/v1/heartbeat" and gpu_list:
                        # Live GPU usage, kept 7 days like the CPU samples; absent from older agents.
                        for stat in gpus.normalized_stats(request.get("gpu_stats")):
                            connection.execute(
                                "INSERT INTO gpu_usage_samples(node_name,gpu_index,util_percent,memory_used_bytes,recorded) "
                                "VALUES(?,?,?,?,?)", (request["node_name"], stat["index"], stat["util_percent"],
                                                      stat["memory_used_bytes"], now))
                        connection.execute("DELETE FROM gpu_usage_samples WHERE recorded<?", (now - 7 * 86400,))
                    return {"ok": True, "heartbeat_seconds": min(10.0, lease_seconds / 3),
                            "storage_validation_mode": validation_mode}

                if route == "/v1/revalidation-batch":
                    node = recovery.require_node(connection, request)
                    records = recovery.revalidation_batch(connection, node["node_name"])
                    return {"mode": node["storage_validation_mode"], "records": records}

                if route == "/v1/revalidation-batch-done":
                    node = recovery.require_node(connection, request)
                    records = request.get("records")
                    if not isinstance(records, list) or len(records) > 512:
                        raise ValueError("invalid revalidation batch")
                    for record in records:
                        kind, digest, valid = record.get("kind"), record.get("digest"), record.get("valid")
                        if kind not in {"artifact", "checkpoint"} or not valid_digest(digest) or type(valid) is not bool:
                            raise ValueError("invalid revalidation result")
                        pending = connection.execute(
                            "SELECT 1 FROM node_revalidation WHERE node_name=? AND kind=? AND digest=?",
                            (node["node_name"], kind, digest),
                        ).fetchone()
                        if pending is None:
                            raise ValueError("revalidation result is not pending")
                        if valid and kind == "artifact":
                            connection.execute(
                                "INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) "
                                "VALUES(?,?,?,?)", (digest, node["node_name"],
                                f"{node['address'].rstrip('/')}/blobs/{digest}", now))
                        elif valid:
                            recovery.acknowledge(connection, digest, node["node_name"], now)
                        connection.execute(
                            "DELETE FROM node_revalidation WHERE node_name=? AND kind=? AND digest=?",
                            (node["node_name"], kind, digest),
                        )
                    remaining = connection.execute(
                        "SELECT COUNT(*) FROM node_revalidation WHERE node_name=?",
                        (node["node_name"],),
                    ).fetchone()[0]
                    if not remaining:
                        connection.execute(
                            "UPDATE nodes SET storage_validation_mode='verified' WHERE node_name=?",
                            (node["node_name"],))
                    return {"ok": True, "remaining": remaining}

                if route == "/v1/gc-plan":
                    node = recovery.require_node(connection, request)
                    return retention.collect_plan(connection, node["node_name"], now)

                if route == "/v1/work-sweep":
                    recovery.require_node(connection, request)
                    ids = request.get("run_ids", [])

                    if not isinstance(ids, list) or len(ids) > 256 or not all(isinstance(item, str) and len(item) == 36 for item in ids):
                        raise ValueError("invalid scratch sweep request")

                    marks = ",".join("?" for _ in ids)
                    found = dict(connection.execute(
                        f"SELECT run_id,state FROM runs WHERE run_id IN ({marks})", ids).fetchall()) if ids else {}
                    return {"states": {item: found.get(item) for item in ids}}

                if route == "/v1/gc-done":
                    node = recovery.require_node(connection, request)
                    hashes = request.get("blob_hashes", [])

                    if not isinstance(hashes, list) or len(hashes) > 128 or not all(valid_digest(digest) for digest in hashes):
                        raise ValueError("invalid garbage collection acknowledgment")

                    connection.executemany(
                        "DELETE FROM checkpoint_garbage WHERE node_name=? AND blob_hash=?",
                        [(node["node_name"], digest) for digest in hashes],
                    )
                    connection.executemany(
                        "DELETE FROM artifact_trim WHERE node_name=? AND artifact_hash=?",
                        [(node["node_name"], digest) for digest in hashes],
                    )
                    return {"ok": True}

                if route in {"/v1/replication-renew", "/v1/replication-release"}:
                    node = recovery.require_node(connection, request)
                    digest, token = request["artifact_hash"], request["transfer_token"]
                    if not valid_digest(digest) or not isinstance(token, str) or len(token) != 36:
                        raise ValueError("invalid replication assignment")
                    if route == "/v1/replication-renew":
                        replication.renew(connection, node, digest, token, now)
                    else:
                        replication.release(connection, node, digest, token)
                    return {"ok": True}

                if route == "/v1/revalidate-done":
                    node = recovery.require_node(connection, request)
                    if request["kind"] not in {"artifact", "checkpoint"} or not valid_digest(request["digest"]):
                        raise ValueError("invalid retained-content acknowledgment")
                    connection.execute("DELETE FROM node_revalidation WHERE node_name=? AND kind=? AND digest=?",
                                       (node["node_name"], request["kind"], request["digest"]))
                    return {"ok": True}

                if route == "/v1/blob-bad":
                    recovery.require_node(connection, request)

                    if not valid_digest(request["blob_hash"]):
                        raise ValueError("invalid damaged blob digest")

                    recovery.invalidate_blob(connection, request["source_node"], request["blob_hash"])
                    return {"ok": True}

                if route in {"/v1/replica", "/v1/checkpoint-replica"}:
                    node = recovery.require_node(connection, request)

                    if route == "/v1/checkpoint-replica":
                        recovery.acknowledge(connection, request["manifest_hash"], node["node_name"], now)
                        return {"ok": True}

                    if connection.execute(
                        "SELECT 1 FROM artifacts WHERE artifact_hash=?", (request["artifact_hash"],),
                    ).fetchone() is None:
                        raise ValueError("unknown artifact")

                    token = request.get("transfer_token")
                    if token is not None:
                        replication.renew(connection, node, request["artifact_hash"], token, now)

                    connection.execute(
                        "INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                        (request["artifact_hash"], node["node_name"], request["location"], now),
                    )
                    connection.execute(
                        "DELETE FROM artifact_trim WHERE artifact_hash=? AND node_name=?",
                        (request["artifact_hash"], node["node_name"]),
                    )
                    if token is not None:
                        replication.release(connection, node, request["artifact_hash"], token)
                    push_copy(connection, request["artifact_hash"], now, lease_seconds)
                    return {"ok": True}

                if route == "/v1/lease":
                    node = recovery.require_node(connection, request)

                    if not node["compute_enabled"]:
                        return {"job": None, "campaign_state": "storage-only"}
                    if connection.execute("SELECT 1 FROM node_dispatch_pauses WHERE node_name=?",
                                          (node["node_name"],)).fetchone() is not None:
                        return {"job": None, "campaign_state": "draining"}

                    slots = normalized_slots(json.loads(node["slots_json"] or "[]"),
                                             node["cpu_set"])
                    requested_slot = request.get("slot_id", 0)
                    if type(requested_slot) is not int:
                        raise ValueError("invalid slot identity")
                    slot = next((item for item in slots
                                 if item["slot_id"] == requested_slot), None)
                    if slot is None:
                        raise ValueError("unregistered slot identity")
                    if connection.execute(
                        "SELECT 1 FROM runs WHERE node_name=? AND state='running' "
                        "AND (slot_id=? OR exclusive_host=1)",
                        (node["node_name"], requested_slot),
                    ).fetchone() is not None or connection.execute(
                        "SELECT 1 FROM node_reservations WHERE node_name=?", (node["node_name"],),
                    ).fetchone() is not None:
                        return {"job": None, "campaign_state": "busy"}
                    campaign = connection.execute(
                        "SELECT value FROM settings WHERE key='campaign_state'"
                    ).fetchone()[0]

                    if campaign != "running":
                        return {"job": None, "campaign_state": campaign}

                    # A full disk fails writes mid-run. Running work continues; new work waits for space
                    # (the leader's garbage collection and trimming free it).
                    if low_disk(node, disk_floor(connection)):
                        return {"job": None, "campaign_state": campaign, "reason": "low disk"}

                    candidates = connection.execute(
                        "SELECT run_id, specification, priority, progress_phase, from_scratch, lease_attempt, engine_failures, "
                        "created, estimated_seconds, "
                        "CASE WHEN parent_run_id IS NULL THEN 0 ELSE "
                        "(SELECT COUNT(*) FROM runs active "
                        "WHERE active.parent_run_id=runs.parent_run_id "
                        "AND active.state='running') END AS peers FROM runs "
                        "WHERE state='queued' AND NOT EXISTS (SELECT 1 FROM lease_history h WHERE h.run_id=runs.run_id "
                        "AND h.node_name=? AND h.outcome='engine retry' AND h.finished>?) "
                        "ORDER BY (progress_phase='reconstructing') DESC, "
                        "priority DESC, "
                        "CASE WHEN parent_run_id IS NULL THEN 0 ELSE "
                        "(SELECT COUNT(*) FROM runs active "
                        "WHERE active.parent_run_id=runs.parent_run_id "
                        "AND active.state='running') END ASC, "
                        "estimated_seconds ASC, created ASC, run_id ASC LIMIT 100",
                        (node["node_name"], now-30),
                    ).fetchall()
                    candidates = locality_order(connection, node["node_name"],
                                                [dict(candidate) for candidate in candidates], now)
                    row = None
                    selected = []
                    live_compute = connection.execute(
                        "SELECT COUNT(*) FROM nodes n WHERE compute_enabled=1 AND last_heartbeat>? "
                        "AND NOT EXISTS (SELECT 1 FROM node_dispatch_pauses pause WHERE pause.node_name=n.node_name)",
                        (now-lease_seconds,),
                    ).fetchone()[0]
                    for candidate in candidates:
                        specification = json.loads(candidate["specification"])
                        adapter = adapters.get(specification)
                        required = adapter.required_nodes(specification)
                        if type(required) is not int or not 1 <= required <= 256:
                            raise ValueError("adapter requested invalid simultaneous node count")
                        resources = ResourceRequest.from_adapter(
                            adapter.resource_requirements(specification))
                        sharing = required == 1 and bool(adapter.allows_host_sharing(specification))
                        active_on_host = connection.execute(
                            "SELECT COUNT(*) AS count,COALESCE(SUM(reserved_memory_bytes),0) AS memory "
                            "FROM runs WHERE node_name=? AND state='running'",
                            (node["node_name"],),
                        ).fetchone()
                        if not sharing and active_on_host["count"]:
                            if candidate["progress_phase"] == "reconstructing":
                                target = reconstruction_drain_target(
                                    connection, now, lease_seconds, resources)
                                if target == node["node_name"]:
                                    # Leave this host's freed slots idle until its tiles finish.
                                    return {"job": None, "campaign_state": campaign}
                            continue
                        participant = dict(node)
                        participant["cpu_set"] = node["cpu_set"]
                        if sharing and node["cpu_set"]:
                            # Slots identify supervisor loops, not fixed one-core
                            # solver allocations. Fence the entire granted team in
                            # this transaction so no two leases overlap.
                            occupied = {cpu for active in connection.execute(
                                "SELECT assigned_cpu_set FROM runs "
                                "WHERE node_name=? AND state='running'",
                                (node["node_name"],))
                                for cpu in (active[0] or node["cpu_set"]).split(",")}
                            free = [cpu for cpu in node["cpu_set"].split(",")
                                    if cpu not in occupied]
                            width = adapter.cpu_width(specification, len(free))
                            if type(width) is not int or not 0 <= width <= len(free):
                                raise ValueError("adapter requested invalid CPU width")
                            if width < resources.min_cpu_count:
                                continue
                            participant["cpu_set"] = ",".join(free[:width])
                        required_memory = resources.coordinator_memory_bytes
                        available_memory = max(0, int(node["memory_bytes"]) - 2 * 1024**3 -
                                               int(active_on_host["memory"]))
                        if (not resource_fits(participant, resources, "coordinator") or
                                (int(node["memory_bytes"]) and required_memory > available_memory)):
                            continue
                        gpu_index = None
                        if resources.gpu_memory_bytes:
                            # One GPU lease per device; opportunistic DP tiles share it via a host lock.
                            busy = {row[0] for row in connection.execute(
                                "SELECT gpu_index FROM runs WHERE node_name=? AND state='running' "
                                "AND gpu_index IS NOT NULL", (node["node_name"],))}
                            gpu_index = gpus.choose(gpus.from_record(node), busy,
                                                    resources.gpu_memory_bytes)
                            if required != 1 or gpu_index is None:
                                continue
                        available = connection.execute(
                            "SELECT n.node_name,n.address,n.cpu_set,n.memory_bytes FROM nodes n "
                            "WHERE n.compute_enabled=1 AND n.last_heartbeat>? AND n.node_name<>? "
                            "AND NOT EXISTS (SELECT 1 FROM node_dispatch_pauses pause WHERE pause.node_name=n.node_name) "
                            "AND NOT EXISTS (SELECT 1 FROM runs active WHERE active.node_name=n.node_name AND active.state='running') "
                            "AND NOT EXISTS (SELECT 1 FROM node_reservations reserve WHERE reserve.node_name=n.node_name) "
                            "ORDER BY n.node_name",
                            (now-lease_seconds, node["node_name"]),
                        ).fetchall() if required > 1 else []
                        selected = [partner for partner in available
                                    if resource_fits(partner, resources, "worker")][:required-1]
                        if len(selected) == required-1:
                            row = candidate
                            break
                        # Let idle nodes accumulate for the highest-priority runnable
                        # group instead of immediately consuming lower-priority
                        # single-node work. Without this barrier a continually fed DP
                        # queue can starve a distributed matching forever. An impossible
                        # manual request must not block the campaign.
                        if (required > 1 and required <= live_compute and
                                candidate["priority"] == candidates[0]["priority"]):
                            return {"job": None, "campaign_state": campaign}
                    if row is None:
                        return {"job": None, "campaign_state": campaign}

                    token = str(uuid.uuid4())
                    connection.execute(
                        "UPDATE runs SET state='running', started=?, node_name=?, lease_token=?, "
                        "stop_requested=0, progress_phase='starting', last_solver_heartbeat=NULL, "
                        "last_progress_at=NULL, last_checkpoint_at=NULL, progress_done=0, "
                        "progress_checkpoint_done=0, lease_expires=?, lease_attempt=lease_attempt+1, "
                        "restored_hash=NULL, restored_done=0, error=NULL,slot_id=?,"
                        "assigned_cpu_set=?,reserved_memory_bytes=?,exclusive_host=?,gpu_index=? "
                        "WHERE run_id=? AND state='queued'",
                        (now, request["node_name"], token, now + lease_seconds,
                         requested_slot, participant["cpu_set"], required_memory,
                         int(not sharing), gpu_index, row["run_id"]),
                    )
                    connection.execute(
                        "INSERT INTO lease_history(lease_token,run_id,node_name,attempt,started) VALUES(?,?,?,?,?)",
                        (token, row["run_id"], node["node_name"], row["lease_attempt"] + 1, now),
                    )
                    connection.executemany(
                        "INSERT INTO node_reservations(node_name,run_id,lease_token,created) VALUES(?,?,?,?)",
                        [(partner["node_name"], row["run_id"], token, now) for partner in selected],
                    )
                    return {
                        "job": {
                            "run_id": row["run_id"],
                            "engine_failures": row["engine_failures"],
                            "lease_attempt": row["lease_attempt"] + 1,
                            "node_name": node["node_name"],
                            "session_id": node["session_id"],
                            "lease_seconds": lease_seconds,
                            "max_checkpoint_bytes": int(recovery.setting(connection, "max_checkpoint_bytes")),
                            "lease_token": token,
                            "slot_id": requested_slot,
                            "assigned_cpu_set": participant["cpu_set"],
                            "gpu_index": gpu_index,
                            "reserved_workers": [dict(partner) for partner in selected],
                            "specification": json.loads(row["specification"]),
                            "from_scratch": bool(row["from_scratch"]),
                            "checkpoint_seconds": int(connection.execute(
                                "SELECT value FROM settings WHERE key='checkpoint_seconds'"
                            ).fetchone()[0]),
                        },
                        "campaign_state": campaign,
                    }

                if route == "/v1/peer-authorize":
                    node = recovery.require_node(connection, request)
                    row = connection.execute(
                        "SELECT * FROM runs WHERE run_id=? AND lease_token=? AND state='running'",
                        (request["run_id"], request["lease_token"]),
                    ).fetchone()
                    reservation = connection.execute(
                        "SELECT 1 FROM node_reservations WHERE node_name=? AND run_id=? AND lease_token=?",
                        (node["node_name"], request["run_id"], request["lease_token"]),
                    ).fetchone()
                    if row is None or reservation is None or row["stop_requested"]:
                        raise PermissionError("peer worker reservation is no longer active")
                    partners = connection.execute(
                        "SELECT node_name FROM node_reservations WHERE run_id=? AND lease_token=? "
                        "ORDER BY node_name",
                        (request["run_id"], request["lease_token"]),
                    ).fetchall()
                    names = [row["node_name"], *(partner["node_name"] for partner in partners)]
                    specification = json.loads(row["specification"])
                    if len(names) != adapters.get(specification).required_nodes(specification):
                        raise PermissionError("peer worker reservation group is incomplete")
                    node_position = names.index(node["node_name"])
                    requested_count = request.get("worker_count", len(names))
                    requested_index = request.get("worker_index", node_position)
                    if (type(requested_count) is not int or type(requested_index) is not int or
                            requested_count != len(names) or requested_index != node_position):
                        raise PermissionError("peer worker shard is outside this reservation")
                    return {"specification": specification,
                            "worker_index": requested_index,
                            "worker_count": requested_count}

                if route in {"/v1/progress", "/v1/resource-usage", "/v1/run-control", "/v1/complete", "/v1/fail", "/v1/requeue",
                             "/v1/checkpoint", "/v1/recovery", "/v1/restored"} or adapters.input_route(route):
                    row = connection.execute(
                        "SELECT * FROM runs WHERE run_id=?",
                        (request["run_id"],),
                    ).fetchone()

                    if (row is None or row["lease_token"] != request["lease_token"] or
                            row["state"] != "running" or row["lease_expires"] is None or
                            row["lease_expires"] <= now):
                        raise PermissionError("stale or invalid lease")

                    if route == "/v1/resource-usage":
                        component = request.get("component")
                        shard_index = request.get("shard_index")
                        cpu_microseconds = request.get("cpu_microseconds")
                        peak_rss_bytes = request.get("peak_rss_bytes")
                        if (component not in {"solver", "coordinator", "shard"} or
                                type(shard_index) is not int or
                                type(cpu_microseconds) is not int or
                                type(peak_rss_bytes) is not int or
                                not 0 <= cpu_microseconds < 2**63 or
                                not 0 <= peak_rss_bytes < 2**63):
                            raise ValueError("invalid resource usage record")
                        partners = connection.execute(
                            "SELECT node_name FROM node_reservations WHERE run_id=? AND lease_token=? "
                            "ORDER BY node_name",
                            (row["run_id"], row["lease_token"]),
                        ).fetchall()
                        names = [row["node_name"], *(partner["node_name"] for partner in partners)]
                        if component == "coordinator":
                            if shard_index != -1:
                                raise ValueError("invalid coordinator resource identity")
                            node_name = names[0]
                        elif component == "solver":
                            if shard_index != 0 or len(names) != 1:
                                raise ValueError("invalid solver resource identity")
                            node_name = names[0]
                        else:
                            if not 0 <= shard_index < len(names):
                                raise ValueError("invalid shard resource identity")
                            node_name = names[shard_index]
                        connection.execute(
                            "INSERT INTO resource_usage(run_id,lease_token,node_name,component,shard_index,"
                            "cpu_microseconds,peak_rss_bytes,recorded) VALUES(?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(lease_token,component,shard_index) DO UPDATE SET "
                            "cpu_microseconds=MAX(cpu_microseconds,excluded.cpu_microseconds),"
                            "peak_rss_bytes=MAX(peak_rss_bytes,excluded.peak_rss_bytes),recorded=excluded.recorded",
                            (row["run_id"], row["lease_token"], node_name, component, shard_index,
                             cpu_microseconds, peak_rss_bytes, now),
                        )
                        connection.execute(
                            "INSERT INTO resource_usage_samples(run_id,lease_token,node_name,component,"
                            "shard_index,cpu_microseconds,rss_bytes,recorded) VALUES(?,?,?,?,?,?,?,?)",
                            (row["run_id"], row["lease_token"], node_name, component, shard_index,
                             cpu_microseconds, peak_rss_bytes, now),
                        )
                        # Every report used to prune: with no index on recorded that scanned
                        # all ~200,000 samples inside the writer lock a few times a second,
                        # about a quarter of the leader's lock time. Once a minute is enough.
                        if now - SAMPLE_PRUNED[0] >= SAMPLE_PRUNE_SECONDS:
                            SAMPLE_PRUNED[0] = now
                            connection.execute(
                                "DELETE FROM resource_usage_samples WHERE recorded<?", (now - 7 * 86400,))
                        return {"ok": True}

                    if adapters.input_route(route):
                        adapter = adapters.get(json.loads(row["specification"]))
                        if route not in adapter.input_routes:
                            raise ValueError("input route does not belong to this solver")
                        return adapter.inputs(connection, row, request, now)

                    if route == "/v1/checkpoint":
                        return recovery.publish(connection, row, request, now)

                    if route == "/v1/recovery":
                        return recovery.recover(connection, row, request, now)

                    if route == "/v1/restored":
                        snapshot = connection.execute(
                            "SELECT * FROM checkpoints WHERE manifest_hash=? AND run_id=? AND retired_at IS NULL",
                            (request["manifest_hash"], row["run_id"]),
                        ).fetchone()

                        if snapshot is None:
                            raise ValueError("unknown recovery checkpoint")

                        connection.execute(
                            "UPDATE runs SET restored_hash=?,restored_done=?,progress_done=?,"
                            "progress_checkpoint_done=?,progress_phase='restored' WHERE run_id=?",
                            (snapshot["manifest_hash"], snapshot["done"], snapshot["done"], snapshot["done"], row["run_id"]),
                        )
                        connection.execute(
                            "UPDATE lease_history SET restored_hash=?,restored_done=? WHERE lease_token=?",
                            (snapshot["manifest_hash"], snapshot["done"], row["lease_token"]),
                        )
                        recovery.acknowledge(connection, snapshot["manifest_hash"], row["node_name"], now)
                        connection.execute("DELETE FROM checkpoint_pins WHERE lease_token=?", (row["lease_token"],))
                        return {"ok": True}

                    if route == "/v1/run-control":
                        connection.execute(
                            "UPDATE runs SET lease_expires=? WHERE run_id=?", (now + lease_seconds, row["run_id"]),
                        )
                        campaign = connection.execute(
                            "SELECT value FROM settings WHERE key='campaign_state'"
                        ).fetchone()[0]
                        return {
                            "campaign_state": campaign,
                            "lease_seconds": lease_seconds,
                            "stop_requested": bool(row["stop_requested"]) or campaign == "stopped",
                        }

                    if route == "/v1/progress":
                        done = int(request.get("done", 0))
                        total = int(request.get("total", 0))
                        checkpoint_done = int(request.get("checkpoint_done", done))

                        # A heartbeat does not imply mathematical progress or durability.
                        if not 0 <= checkpoint_done <= done <= total:
                            raise ValueError("invalid progress or checkpoint counts")

                        core = {"run_id", "lease_token", "event", "done", "total",
                                "checkpoint_done", "message", "phase", "units",
                                "heartbeat"}
                        details = {}
                        for key, value in request.items():
                            if key in core:
                                continue
                            if (not isinstance(key, str) or len(details) >= 32 or
                                    not isinstance(value, (str, int, float, bool, type(None)))):
                                continue
                            details[key[:64]] = value[:256] if isinstance(value, str) else value
                        encoded_details = canonical_json(details).decode("utf-8")

                        advanced = done > row["progress_done"]
                        checkpoint_advanced = checkpoint_done > row["progress_checkpoint_done"]
                        connection.execute(
                            "UPDATE runs SET progress_done=?, progress_total=?, progress_message=?, "
                            "progress_checkpoint_done=?, progress_phase=?, progress_units=?, progress_details=?, "
                            "last_solver_heartbeat=?, last_progress_at=?, last_checkpoint_at=? WHERE run_id=?",
                            (
                                done,
                                total,
                                str(request.get("message", "")),
                                checkpoint_done,
                                str(request.get("phase", "computing")),
                                str(request.get("units", "steps")),
                                encoded_details,
                                now,
                                now if advanced or row["last_progress_at"] is None else row["last_progress_at"],
                                now if checkpoint_advanced else row["last_checkpoint_at"],
                                request["run_id"],
                            ),
                        )
                        return {"ok": True}
                    elif route == "/v1/complete":
                        if not valid_digest(request["artifact_hash"]):
                            raise ValueError("invalid artifact digest")

                        run = connection.execute(
                            "SELECT node_name FROM runs WHERE run_id=?",
                            (request["run_id"],),
                        ).fetchone()
                        connection.execute(
                            "UPDATE runs SET state='complete', finished=?, artifact_hash=?, "
                            "artifact_location=?, progress_phase='complete', progress_message=CASE WHEN substr(progress_message,1,1)='{' "
                            "THEN progress_message ELSE 'complete' END WHERE run_id=?",
                            (
                                now,
                                request["artifact_hash"],
                                request["artifact_location"],
                                request["run_id"],
                            ),
                        )
                        connection.execute(
                            "INSERT OR IGNORE INTO artifacts(artifact_hash, target_replicas, created,size) "
                            "VALUES(?, 3, ?,?)",
                            (request["artifact_hash"], now, request.get("artifact_size")),
                        )
                        connection.execute(
                            "INSERT OR REPLACE INTO replicas(artifact_hash, node_name, location, created) "
                            "VALUES(?, ?, ?, ?)",
                            (
                                request["artifact_hash"],
                                run["node_name"],
                                request["artifact_location"],
                                now,
                            ),
                        )
                        push_copy(connection, request["artifact_hash"], now, lease_seconds)
                    elif route == "/v1/fail":
                        failure_kind = request.get("failure_kind", "engine_failure")
                        if failure_kind not in {"engine_failure", "stop_failure"}:
                            raise ValueError("invalid typed failure outcome")
                        connection.execute(
                            "UPDATE runs SET state='failed', finished=?, error=?, failure_kind=?, "
                            "progress_phase='failed' WHERE run_id=?",
                            (now, str(request.get("error", "unknown failure")), failure_kind,
                             request["run_id"]),
                        )
                    else:
                        reason = request.get("reason", "intentional_stop")
                        if reason not in {"intentional_stop", "engine_failure"}:
                            raise ValueError("invalid typed requeue outcome")
                        target_state = row["control_state"] if row["control_state"] in {"paused", "cancelled"} else "queued"
                        connection.execute(
                            "UPDATE runs SET state=?, node_name=NULL, lease_token=NULL, "
                            "finished=?, progress_message='stopped; checkpoint retained', progress_phase=? WHERE run_id=?",
                            (target_state, now if target_state == "cancelled" else None,
                             target_state if target_state != "queued" else "stopped", request["run_id"]),
                        )

                    outcome = route.rsplit("/", 1)[-1]
                    if route == "/v1/requeue" and request.get("reason") == "engine_failure":
                        connection.execute("UPDATE runs SET engine_failures=engine_failures+1 WHERE run_id=?", (row["run_id"],))
                        outcome = "engine retry"
                    elif route == "/v1/requeue":
                        outcome = request.get("reason", "intentional_stop")
                    connection.execute("DELETE FROM node_reservations WHERE lease_token=?", (row["lease_token"],))
                    connection.execute(
                        "UPDATE lease_history SET finished=?,outcome=? WHERE lease_token=?",
                        (now, outcome, row["lease_token"]),
                    )
                    connection.execute("UPDATE runs SET lease_expires=NULL WHERE run_id=?", (row["run_id"],))
                    return {"ok": True}

            raise ValueError("unknown endpoint")

        # Keep routine access logs out of interactive output.
        def log_message(self, format_string: str, *arguments: Any) -> None:
            """Suppress the base HTTP access log."""

            return

    return Handler


# Find the first complete physical core within the process's allowed CPU set.
def reserved_leader_cpus() -> list[int]:
    """Return all available SMT siblings of the first physical core."""

    cores: dict[tuple[int, int], list[int]] = {}

    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")

        try:
            package = int((topology / "physical_package_id").read_text())
            core = int((topology / "core_id").read_text())
        except (FileNotFoundError, ValueError):
            package = 0
            core = cpu

        cores.setdefault((package, core), []).append(cpu)

    if not cores:
        raise RuntimeError("no CPUs are available to the leader")

    return sorted(cores[sorted(cores)[0]])


# Parse command-line options, showing useful help when no command is supplied.
def build_parser() -> argparse.ArgumentParser:
    """Build the leader command-line parser."""

    parser = argparse.ArgumentParser(
        description="Run the first-draft king_hamming leader.",
        epilog="Example: ./leader.py serve --database state/leader.sqlite --listen 0.0.0.0:8041",
    )
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="initialize and serve the leader database")
    serve.add_argument("--database", type=Path, default=Path("state/leader.sqlite"))
    serve.add_argument("--listen", default="127.0.0.1:8041", metavar="HOST:PORT")
    serve.add_argument("--checkpoint-seconds", type=int, default=1800)
    serve.add_argument("--lease-seconds", type=float, default=60, help="supervisor renewal deadline")
    serve.add_argument("--max-checkpoint-bytes", type=int, default=DEFAULT_MAX_BYTES)
    serve.add_argument("--visits-per-second", type=float, default=100_000_000.0, help="coarse serial raw-visit rate for queue estimates")
    serve.add_argument("--checkpoint-keep", type=int, default=3, help="minimum replicated snapshots retained per run (at least two)")
    serve.add_argument(
        "--pin-leader-core",
        action="store_true",
        help="pin the leader to the first physical core and its SMT siblings",
    )
    return parser


# Retry background maintenance after transient database contention instead of silently dying.
def scheduler_loop(
    database: Path, lease_seconds: float, stop: threading.Event,
    health: SchedulerHealth, connect_timeout: float = 30.0,
) -> None:
    """Expire leases and advance adapter queues until stop is set."""

    # A full DAG pass may inspect tens of thousands of tiles. Ready children
    # remain queued between passes; a three-second cadence frees substantial
    # writer time for lease renewals without starving the frontier.
    interval = min(3.0, lease_seconds / 3)

    while not stop.wait(interval):
        health.attempting()

        try:
            with writer_session(database, "scheduler", health, timeout=connect_timeout) as connection:
                now = time.time()
                recovery.expire(connection, now)
                adapters.advance(connection, now)
        except Exception as error:
            health.failed(error)
            print("scheduler transaction failed; retrying", flush=True)
            traceback.print_exc()
        else:
            health.succeeded()


# Initialize the database and serve until interrupted.
def main() -> int:
    """Run the selected leader command and return a process exit status."""

    parser = build_parser()
    arguments = parser.parse_args()

    if arguments.command is None:
        parser.print_help()
        return 0

    # Each in-flight request holds a socket and a database connection; the
    # common 1024 soft limit leaves too little headroom for a busy fleet.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = 65536 if hard == resource.RLIM_INFINITY else min(hard, 65536)
    if soft != resource.RLIM_INFINITY and soft < wanted:
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))

    if arguments.pin_leader_core:
        leader_cpus = reserved_leader_cpus()
        os.sched_setaffinity(0, leader_cpus)
        print(
            "leader pinned to CPUs " + ",".join(str(cpu) for cpu in leader_cpus),
            flush=True,
        )

    initialize(arguments.database, arguments.checkpoint_seconds, arguments.lease_seconds, arguments.max_checkpoint_bytes, arguments.checkpoint_keep, arguments.visits_per_second)
    host, port_text = arguments.listen.rsplit(":", 1)
    scheduler_health = SchedulerHealth(max(10.0, min(30.0, arguments.lease_seconds / 2)))
    server = ThreadingHTTPServer(
        (host, int(port_text)), make_handler(arguments.database, scheduler_health),
    )
    print(f"leader listening on http://{host}:{port_text}", flush=True)
    stop_reaper = threading.Event()

    reaper = threading.Thread(
        target=scheduler_loop,
        args=(arguments.database, arguments.lease_seconds, stop_reaper, scheduler_health),
        daemon=True,
    )
    reaper.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_reaper.set()
        reaper.join(timeout=5)
        server.server_close()

    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
