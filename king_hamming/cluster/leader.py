#!/usr/bin/env python3
"""Run the king_hamming leader and its durable queue."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import threading
import time
import traceback
import uuid
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
import adapters

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
CREATE TABLE IF NOT EXISTS nodes (
    node_name TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    cpu_set TEXT NOT NULL,
    storage_root TEXT NOT NULL,
    last_heartbeat REAL NOT NULL,
    state TEXT NOT NULL
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
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


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
            healthy = self.consecutive_failures == 0 and not stalled
            return {
                "status": "healthy" if healthy else "stalled" if stalled else "retrying",
                "healthy": healthy,
                "last_attempt": self.last_attempt,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "consecutive_failures": self.consecutive_failures,
            }


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

    with connect(database) as connection:
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
            "parent_run_id": "TEXT REFERENCES runs(run_id)",
        }

        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('campaign_state', 'running')"
        )
        connection.execute(
            "INSERT INTO settings(key, value) VALUES('checkpoint_seconds', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(checkpoint_seconds),),
        )

        recovery.initialize(connection, lease_seconds, max_checkpoint_bytes)
        retention.initialize(connection, checkpoint_keep)
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
                with connect(database) as connection:
                    campaign = connection.execute(
                        "SELECT value FROM settings WHERE key='campaign_state'"
                    ).fetchone()[0]
                    checkpoint_seconds = int(connection.execute(
                        "SELECT value FROM settings WHERE key='checkpoint_seconds'"
                    ).fetchone()[0])
                    runs = [dict(row) for row in connection.execute(
                        "SELECT * FROM runs WHERE parent_run_id IS NULL OR state='running' ORDER BY created DESC LIMIT 200"
                    )]
                    nodes = [dict(row) for row in connection.execute(
                        "SELECT * FROM nodes ORDER BY node_name"
                    )]
                    reservations = {entry["node_name"]: entry["run_id"] for entry in connection.execute(
                        "SELECT node_name,run_id FROM node_reservations"
                    )}
                    for participant in nodes:
                        participant["reserved_for"] = reservations.get(participant["node_name"])
                    recovery.add_status(connection, runs, time.time())
                    for run in runs:
                        counts = connection.execute(
                            "SELECT COUNT(*) AS total,SUM(retired_at IS NULL) AS retained FROM checkpoints WHERE run_id=?",
                            (run["run_id"],),
                        ).fetchone()
                        run["retained_checkpoints"] = counts["retained"] or 0
                        run["retired_checkpoints"] = counts["total"] - run["retained_checkpoints"]
                        run["resource_usage"] = [dict(item) for item in connection.execute(
                            "SELECT u.lease_token,u.node_name,u.component,u.shard_index,"
                            "u.cpu_microseconds,u.peak_rss_bytes,u.recorded,h.attempt "
                            "FROM resource_usage u LEFT JOIN lease_history h USING(lease_token) "
                            "WHERE u.run_id=? ORDER BY COALESCE(h.attempt,0),u.shard_index,u.component",
                            (run["run_id"],),
                        )]
                    lease_seconds = recovery.setting(connection, "lease_seconds")
                    checkpoint_keep = int(recovery.setting(connection, "checkpoint_keep"))
                    artifacts = [dict(row) for row in connection.execute(
                        "SELECT a.artifact_hash, a.target_replicas, SUM(CASE WHEN n.last_heartbeat>? THEN 1 ELSE 0 END) AS replicas, COUNT(r.node_name) AS indexed_replicas "
                        "FROM artifacts a LEFT JOIN replicas r USING(artifact_hash) LEFT JOIN nodes n USING(node_name) "
                        "WHERE EXISTS (SELECT 1 FROM runs root WHERE root.artifact_hash=a.artifact_hash AND root.parent_run_id IS NULL) "
                        "GROUP BY a.artifact_hash ORDER BY a.created DESC LIMIT 200", (time.time()-lease_seconds,),
                    )]

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

        # Apply one protocol operation transactionally.
        def dispatch_post(self, route: str, request: dict[str, Any]) -> dict[str, Any]:
            """Apply route using request and return its response object."""

            with connect(database) as connection:
                # Serialize lease validation and mutation to fence racing stale submissions.
                connection.execute("BEGIN IMMEDIATE")
                now = time.time()
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

                if route in {"/v1/register", "/v1/heartbeat"}:
                    existing = connection.execute(
                        "SELECT * FROM nodes WHERE node_name=?", (request["node_name"],),
                    ).fetchone()
                    session_id = str(request.get("session_id", ""))

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

                    connection.execute(
                        "INSERT INTO nodes(node_name,address,cpu_set,storage_root,last_heartbeat,state,session_id,compute_enabled) "
                        "VALUES(?,?,?,?,?,'healthy',?,?) ON CONFLICT(node_name) DO UPDATE SET "
                        "address=excluded.address,cpu_set=excluded.cpu_set,storage_root=excluded.storage_root, "
                        "last_heartbeat=excluded.last_heartbeat,state='healthy',session_id=excluded.session_id, "
                        "compute_enabled=excluded.compute_enabled",
                        (request["node_name"], request.get("address", ""), request.get("cpu_set", ""),
                         request.get("storage_root", ""), now, session_id, int(not request.get("storage_only", False))),
                    )
                    return {"ok": True, "heartbeat_seconds": min(10.0, lease_seconds / 3)}

                if route == "/v1/gc-plan":
                    node = recovery.require_node(connection, request)
                    return retention.collect_plan(connection, node["node_name"], now)

                if route == "/v1/gc-done":
                    node = recovery.require_node(connection, request)
                    hashes = request.get("blob_hashes", [])

                    if not isinstance(hashes, list) or len(hashes) > 128 or not all(valid_digest(digest) for digest in hashes):
                        raise ValueError("invalid garbage collection acknowledgment")

                    connection.executemany(
                        "DELETE FROM checkpoint_garbage WHERE node_name=? AND blob_hash=?",
                        [(node["node_name"], digest) for digest in hashes],
                    )
                    return {"ok": True}

                if route == "/v1/replication":
                    node = recovery.require_node(connection, request)
                    inventory = recovery.revalidation(connection, node["node_name"], now)
                    if inventory is not None:
                        return {"replication": inventory}
                    checkpoint = recovery.replication(connection, node["node_name"], now)

                    if checkpoint is not None:
                        return {"replication": checkpoint}

                    row = connection.execute(
                        "SELECT a.artifact_hash,a.size FROM artifacts a WHERE NOT EXISTS "
                        "(SELECT 1 FROM replicas own WHERE own.artifact_hash=a.artifact_hash AND own.node_name=?) "
                        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
                        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?) < a.target_replicas "
                        "AND EXISTS (SELECT 1 FROM replicas r JOIN nodes n USING(node_name) "
                        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?) "
                        "ORDER BY a.created LIMIT 1", (node["node_name"], now - lease_seconds, now - lease_seconds),
                    ).fetchone()

                    if row is None:
                        return {"replication": None}

                    locations = [record[0] for record in connection.execute(
                        "SELECT r.location FROM replicas r JOIN nodes n USING(node_name) "
                        "WHERE r.artifact_hash=? AND n.last_heartbeat>? ORDER BY n.node_name",
                        (row["artifact_hash"], now - lease_seconds),
                    )]
                    return {"replication": {**dict(row), "kind": "artifact", "locations": locations,
                                             "location": locations[0]}}

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

                    connection.execute(
                        "INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                        (request["artifact_hash"], node["node_name"], request["location"], now),
                    )
                    return {"ok": True}

                if route == "/v1/lease":
                    node = recovery.require_node(connection, request)

                    if not node["compute_enabled"]:
                        return {"job": None, "campaign_state": "storage-only"}

                    if connection.execute(
                        "SELECT 1 FROM runs WHERE node_name=? AND state='running'", (node["node_name"],),
                    ).fetchone() is not None or connection.execute(
                        "SELECT 1 FROM node_reservations WHERE node_name=?", (node["node_name"],),
                    ).fetchone() is not None:
                        return {"job": None, "campaign_state": "busy"}
                    campaign = connection.execute(
                        "SELECT value FROM settings WHERE key='campaign_state'"
                    ).fetchone()[0]

                    if campaign != "running":
                        return {"job": None, "campaign_state": campaign}

                    candidates = connection.execute(
                        "SELECT run_id, specification, from_scratch, lease_attempt, engine_failures FROM runs "
                        "WHERE state='queued' AND NOT EXISTS (SELECT 1 FROM lease_history h WHERE h.run_id=runs.run_id "
                        "AND h.node_name=? AND h.outcome='engine retry' AND h.finished>?) "
                        "ORDER BY priority DESC, estimated_seconds ASC, created ASC, run_id ASC LIMIT 100",
                        (node["node_name"], now-30),
                    ).fetchall()
                    row = None
                    selected = []
                    for candidate in candidates:
                        specification = json.loads(candidate["specification"])
                        required = adapters.get(specification).required_nodes(specification)
                        if type(required) is not int or not 1 <= required <= 8:
                            raise ValueError("adapter requested invalid simultaneous node count")
                        selected = connection.execute(
                            "SELECT n.node_name,n.address,n.cpu_set FROM nodes n "
                            "WHERE n.compute_enabled=1 AND n.last_heartbeat>? AND n.node_name<>? "
                            "AND NOT EXISTS (SELECT 1 FROM runs active WHERE active.node_name=n.node_name AND active.state='running') "
                            "AND NOT EXISTS (SELECT 1 FROM node_reservations reserve WHERE reserve.node_name=n.node_name) "
                            "ORDER BY n.node_name LIMIT ?",
                            (now-lease_seconds, node["node_name"], required-1),
                        ).fetchall() if required > 1 else []
                        if len(selected) == required-1:
                            row = candidate
                            break
                    if row is None:
                        return {"job": None, "campaign_state": campaign}

                    token = str(uuid.uuid4())
                    connection.execute(
                        "UPDATE runs SET state='running', started=?, node_name=?, lease_token=?, "
                        "stop_requested=0, progress_phase='starting', last_solver_heartbeat=NULL, "
                        "last_progress_at=NULL, last_checkpoint_at=NULL, progress_done=0, "
                        "progress_checkpoint_done=0, lease_expires=?, lease_attempt=lease_attempt+1, "
                        "restored_hash=NULL, restored_done=0, error=NULL "
                        "WHERE run_id=? AND state='queued'",
                        (now, request["node_name"], token, now + lease_seconds, row["run_id"]),
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

                    if row is None or row["lease_token"] != request["lease_token"] or row["state"] != "running":
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

                        advanced = done > row["progress_done"]
                        checkpoint_advanced = checkpoint_done > row["progress_checkpoint_done"]
                        connection.execute(
                            "UPDATE runs SET progress_done=?, progress_total=?, progress_message=?, "
                            "progress_checkpoint_done=?, progress_phase=?, progress_units=?, "
                            "last_solver_heartbeat=?, last_progress_at=?, last_checkpoint_at=? WHERE run_id=?",
                            (
                                done,
                                total,
                                str(request.get("message", "")),
                                checkpoint_done,
                                str(request.get("phase", "computing")),
                                str(request.get("units", "steps")),
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
                            "artifact_location=?, progress_message='complete', progress_phase='complete' WHERE run_id=?",
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
                    elif route == "/v1/fail":
                        connection.execute(
                            "UPDATE runs SET state='failed', finished=?, error=?, progress_phase='failed' WHERE run_id=?",
                            (now, str(request.get("error", "unknown failure")), request["run_id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE runs SET state='queued', node_name=NULL, lease_token=NULL, "
                            "progress_message='stopped; checkpoint retained', progress_phase='stopped' WHERE run_id=?",
                            (request["run_id"],),
                        )

                    outcome = route.rsplit("/", 1)[-1]
                    if route == "/v1/requeue" and request.get("engine_failure", False):
                        connection.execute("UPDATE runs SET engine_failures=engine_failures+1 WHERE run_id=?", (row["run_id"],))
                        outcome = "engine retry"
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

    interval = min(1.0, lease_seconds / 3)

    while not stop.wait(interval):
        health.attempting()

        try:
            with connect(database, timeout=connect_timeout) as connection:
                connection.execute("BEGIN IMMEDIATE")
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
