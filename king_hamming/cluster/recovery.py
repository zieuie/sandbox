"""Transactional lease fencing and replicated-checkpoint indexing for the leader."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from checkpoints import validate_manifest
from common import calculation_id, canonical_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    manifest_hash TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    cursor INTEGER NOT NULL,
    done INTEGER NOT NULL,
    manifest TEXT NOT NULL,
    created REAL NOT NULL,
    durable_at REAL
);
CREATE INDEX IF NOT EXISTS checkpoints_run ON checkpoints(run_id, cursor DESC);
CREATE TABLE IF NOT EXISTS node_reservations (
    node_name TEXT PRIMARY KEY REFERENCES nodes(node_name),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    lease_token TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS reservations_by_lease ON node_reservations(lease_token);
CREATE TABLE IF NOT EXISTS checkpoint_replicas (
    manifest_hash TEXT NOT NULL REFERENCES checkpoints(manifest_hash),
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    created REAL NOT NULL,
    PRIMARY KEY(manifest_hash, node_name)
);
CREATE TABLE IF NOT EXISTS node_revalidation (
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    kind TEXT NOT NULL,
    digest TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY(node_name,kind,digest)
);
CREATE TABLE IF NOT EXISTS lease_history (
    lease_token TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    node_name TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    started REAL NOT NULL,
    finished REAL,
    outcome TEXT NOT NULL DEFAULT 'running',
    restored_hash TEXT,
    restored_done INTEGER NOT NULL DEFAULT 0
);
"""


# Upgrade existing databases without deleting run or snapshot history.
def initialize(connection: sqlite3.Connection, lease_seconds: float, max_bytes: int) -> None:
    """Create recovery schema/settings using connection and the supplied limits."""

    connection.executescript(SCHEMA)
    additions = {
        "runs": {"lease_expires": "REAL", "lease_attempt": "INTEGER NOT NULL DEFAULT 0",
                 "recovery_count": "INTEGER NOT NULL DEFAULT 0", "engine_failures": "INTEGER NOT NULL DEFAULT 0", "restored_hash": "TEXT",
                 "restored_done": "INTEGER NOT NULL DEFAULT 0"},
        "nodes": {"session_id": "TEXT NOT NULL DEFAULT ''", "compute_enabled": "INTEGER NOT NULL DEFAULT 1",
                  "memory_bytes": "INTEGER NOT NULL DEFAULT 0",
                  "runtime_version": "TEXT NOT NULL DEFAULT ''",
                  "physical_core_count": "INTEGER NOT NULL DEFAULT 0",
                  "storage_generation": "TEXT NOT NULL DEFAULT ''",
                  "storage_validation_mode": "TEXT NOT NULL DEFAULT 'verified'",
                  "slots_json": "TEXT NOT NULL DEFAULT '[]'"},
        "artifacts": {"size": "INTEGER"},
        # 0 while a restarted agent has not yet re-proved this copy on its disk (see /v1/register).
        "replicas": {"verified": "INTEGER NOT NULL DEFAULT 1"},
        "checkpoint_replicas": {"verified": "INTEGER NOT NULL DEFAULT 1"},
    }

    for table, fields in additions.items():
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}

        for name, definition in fields.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    for name, value in (("lease_seconds", lease_seconds), ("max_checkpoint_bytes", max_bytes)):
        connection.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (name, str(value)),
        )

    # Pre-upgrade leases cannot be safely renewed under the new fencing contract.
    connection.execute(
        "UPDATE runs SET state='queued', node_name=NULL, lease_token=NULL, "
        "progress_phase='recovering' WHERE state='running' AND lease_expires IS NULL"
    )


def restore_unverified_claims(connection: sqlite3.Connection) -> None:
    """Turn revalidation entries left by older leaders back into unverified claims.

    Leaders before the verified column deleted a restarted agent's claims and kept only the
    revalidation queue. Run after every table and column exists.
    """

    connection.execute(
        "INSERT OR IGNORE INTO replicas(artifact_hash,node_name,location,created,verified) "
        "SELECT v.digest,v.node_name,rtrim(n.address,'/')||'/blobs/'||v.digest,v.created,0 "
        "FROM node_revalidation v JOIN nodes n USING(node_name) JOIN artifacts a ON a.artifact_hash=v.digest "
        "WHERE v.kind='artifact'")
    connection.execute(
        "INSERT OR IGNORE INTO checkpoint_replicas(manifest_hash,node_name,created,verified) "
        "SELECT v.digest,v.node_name,v.created,0 FROM node_revalidation v "
        "JOIN checkpoints c ON c.manifest_hash=v.digest WHERE v.kind='checkpoint' AND c.retired_at IS NULL")


# Read numeric leader settings through one consistent conversion path.
def setting(connection: sqlite3.Connection, name: str) -> float:
    """Return numeric setting name from connection."""

    return float(connection.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()[0])


# Retire a lease atomically before another worker can receive the calculation.
def retire(connection: sqlite3.Connection, row: sqlite3.Row, now: float, reason: str) -> None:
    """Fence row's lease, retain its history, and requeue its run in connection."""

    connection.execute(
        "UPDATE lease_history SET finished=?, outcome=? WHERE lease_token=? AND outcome='running'",
        (now, reason, row["lease_token"]),
    )
    connection.execute("DELETE FROM node_reservations WHERE lease_token=?", (row["lease_token"],))
    target = row["control_state"] if row["control_state"] in {"paused", "cancelled"} else "queued"
    connection.execute(
        "UPDATE runs SET state=?, node_name=NULL, lease_token=NULL, lease_expires=NULL, finished=?, "
        "recovery_count=recovery_count+1, progress_phase=?, progress_message=? WHERE run_id=?",
        (target, now if target == "cancelled" else None,
         "recovering" if target == "queued" else target, reason, row["run_id"]),
    )


# Reassignment depends on the supervising lease, not a synthetic solver heartbeat.
def forgive_stall(connection: sqlite3.Connection, now: float) -> int:
    """Give every running lease at least one full lease from now; return how many were extended.

    For use after the leader itself stalled: agents could not renew while it was stuck.
    """

    return connection.execute(
        "UPDATE runs SET lease_expires=? WHERE state='running' AND lease_expires<?",
        (now + setting(connection, "lease_seconds"), now + setting(connection, "lease_seconds")),
    ).rowcount


def expire(connection: sqlite3.Connection, now: float, partner_grace: float = 0.0) -> None:
    """Retire expired running leases inside the caller's write transaction.

    partner_grace widens the partner-heartbeat window by a leader stall's length.
    """

    rows = connection.execute(
        "SELECT * FROM runs WHERE state='running' AND lease_expires<=?", (now,),
    ).fetchall()

    for row in rows:
        retire(connection, row, now, "lease expired")

    # A reserved partner's missing agent heartbeat fences the entire group.
    cutoff = now - setting(connection, "lease_seconds") - partner_grace
    partners = connection.execute(
        "SELECT DISTINCT r.* FROM runs r JOIN node_reservations reserve ON reserve.run_id=r.run_id "
        "JOIN nodes n ON n.node_name=reserve.node_name "
        "WHERE r.state='running' AND n.last_heartbeat<=?", (cutoff,),
    ).fetchall()
    for row in partners:
        retire(connection, row, now, "reserved partner heartbeat expired")


# Require current agent incarnation for all node-owned operations.
def require_node(connection: sqlite3.Connection, request: dict[str, Any]) -> sqlite3.Row:
    """Return request's registered node or raise PermissionError for a stale incarnation."""

    row = connection.execute("SELECT * FROM nodes WHERE node_name=?", (request["node_name"],)).fetchone()

    if row is None or row["session_id"] != request.get("session_id", ""):
        raise PermissionError("stale or unregistered agent session")

    return row


# Enumerate currently reachable locations of a complete checkpoint replica.
def sources(connection: sqlite3.Connection, digest: str, now: float) -> list[dict[str, Any]]:
    """Return healthy registered node addresses holding all of digest's snapshot files."""

    cutoff = now - setting(connection, "lease_seconds")
    return [dict(row) for row in connection.execute(
        "SELECT n.node_name,n.address FROM checkpoint_replicas r JOIN nodes n USING(node_name) "
        "WHERE r.manifest_hash=? AND r.verified=1 AND n.last_heartbeat>? ORDER BY n.node_name", (digest, cutoff),
    )]


# Materialize the small control description needed for peer-to-peer data transfer.
def task(connection: sqlite3.Connection, row: sqlite3.Row, now: float) -> dict[str, Any]:
    """Return a checkpoint transfer task for row using currently live sources."""

    return {"kind": "checkpoint", "manifest_hash": row["manifest_hash"],
            "manifest": json.loads(row["manifest"]),
            "sources": sources(connection, row["manifest_hash"], now)}


# Register immutable local snapshots only through their still-valid computation lease.
def publish(
    connection: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any], now: float,
) -> dict[str, Any]:
    """Validate and index request's snapshot for run; return its manifest identity."""

    manifest = request["manifest"]
    specification = json.loads(run["specification"])
    validate_manifest(manifest, specification, run["run_id"], int(setting(connection, "max_checkpoint_bytes")))
    digest = calculation_id(manifest)

    if request["manifest_hash"] != digest:
        raise ValueError("checkpoint manifest hash mismatch")

    connection.execute(
        "INSERT OR IGNORE INTO checkpoints(manifest_hash,run_id,cursor,done,manifest,created) "
        "VALUES(?,?,?,?,?,?)",
        (digest, run["run_id"], manifest["cursor"], manifest["done"], canonical_json(manifest).decode(), now),
    )
    # A live calculation may replay an older cursor; its new publication restores ownership.
    connection.execute("UPDATE checkpoints SET retired_at=NULL WHERE manifest_hash=?", (digest,))
    acknowledge(connection, digest, run["node_name"], now)
    return {"ok": True, "manifest_hash": digest}


# Record a whole verified copy, never individual partial members.
def acknowledge(connection: sqlite3.Connection, digest: str, node: str, now: float) -> None:
    """Record node's complete snapshot and mark its first two-copy durability time."""

    if connection.execute("SELECT 1 FROM checkpoints WHERE manifest_hash=? AND retired_at IS NULL", (digest,)).fetchone() is None:
        raise ValueError("unknown checkpoint manifest")

    connection.execute(
        "INSERT OR REPLACE INTO checkpoint_replicas(manifest_hash,node_name,created) VALUES(?,?,?)",
        (digest, node, now),
    )

    if len(sources(connection, digest, now)) >= 2:
        connection.execute(
            "UPDATE checkpoints SET durable_at=COALESCE(durable_at,?) WHERE manifest_hash=?", (now, digest),
        )


# Restore the newest candidate; the worker can exclude invalid or unreachable candidates.
def recover(
    connection: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any], now: float,
) -> dict[str, Any]:
    """Return one recovery candidate for run, retaining all older snapshots for fallback."""

    excluded = request.get("exclude", [])

    if not isinstance(excluded, list) or len(excluded) > 1000:
        raise ValueError("invalid recovery exclusion list")

    connection.execute("DELETE FROM checkpoint_pins WHERE lease_token=?", (run["lease_token"],))
    rows = connection.execute(
        "SELECT * FROM checkpoints WHERE run_id=? AND retired_at IS NULL ORDER BY cursor DESC, created DESC", (run["run_id"],),
    )
    has_checkpoints = connection.execute("SELECT 1 FROM checkpoints WHERE run_id=?", (run["run_id"],)).fetchone() is not None

    for row in rows:
        has_checkpoints = True

        if row["manifest_hash"] not in excluded:
            connection.execute("INSERT OR REPLACE INTO checkpoint_pins(lease_token,manifest_hash) VALUES(?,?)",
                               (run["lease_token"], row["manifest_hash"]))
            return {"checkpoint": task(connection, row, now), "has_checkpoints": True}

    return {"checkpoint": None, "has_checkpoints": has_checkpoints}


# Find under-replicated checkpoints independently of whether this worker is computing.
def replication(connection: sqlite3.Connection, node: str, now: float) -> dict[str, Any] | None:
    """Return one needed checkpoint copy for node, or None when no live source can help."""

    rows = connection.execute(
        "SELECT c.* FROM checkpoints c WHERE c.retired_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM checkpoint_replicas r WHERE r.manifest_hash=c.manifest_hash AND r.node_name=?) "
        "ORDER BY c.created DESC", (node,),
    )

    for row in rows:
        candidate = task(connection, row, now)

        if 0 < len(candidate["sources"]) < 2:
            run = connection.execute("SELECT specification FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
            candidate["specification"] = json.loads(run["specification"])
            candidate["max_checkpoint_bytes"] = int(setting(connection, "max_checkpoint_bytes"))
            return candidate

    return None


# Remove claims for confirmed missing or corrupt content so replication can repair them.
def invalidate_blob(connection: sqlite3.Connection, node: str, digest: str) -> None:
    """Drop node's replica claims referencing digest; preserve all checkpoint manifests/history."""

    connection.execute("DELETE FROM replicas WHERE node_name=? AND artifact_hash=?", (node, digest))
    snapshots = connection.execute(
        "SELECT c.manifest_hash,c.manifest FROM checkpoints c JOIN checkpoint_replicas r USING(manifest_hash) "
        "WHERE r.node_name=?", (node,),
    ).fetchall()

    for snapshot in snapshots:
        manifest = json.loads(snapshot["manifest"])

        if snapshot["manifest_hash"] == digest or any(record["sha256"] == digest for record in manifest["files"]):
            connection.execute(
                "DELETE FROM checkpoint_replicas WHERE node_name=? AND manifest_hash=?",
                (node, snapshot["manifest_hash"]),
            )


# Expose local durability and replicated recovery coverage as separate status fields.
def add_status(connection: sqlite3.Connection, runs: list[dict[str, Any]], now: float) -> None:
    """Augment runs in place with their latest retained replicated snapshot and live copies."""

    if not runs:
        return
    identifiers = [run["run_id"] for run in runs]
    placeholders = ",".join("?" for _ in identifiers)
    snapshots_by_run: dict[str, list[sqlite3.Row]] = {identifier: [] for identifier in identifiers}
    for row in connection.execute(
        f"SELECT * FROM checkpoints WHERE retired_at IS NULL AND run_id IN ({placeholders}) "
        "ORDER BY run_id,cursor DESC,created DESC", identifiers,
    ):
        snapshots_by_run[row["run_id"]].append(row)
    cutoff = now - setting(connection, "lease_seconds")
    live_copies = {row["manifest_hash"]: row["copies"] for row in connection.execute(
        f"SELECT r.manifest_hash,COUNT(*) AS copies FROM checkpoint_replicas r "
        f"JOIN nodes n USING(node_name) JOIN checkpoints c USING(manifest_hash) "
        f"WHERE c.run_id IN ({placeholders}) AND r.verified=1 AND n.last_heartbeat>? GROUP BY r.manifest_hash",
        [*identifiers, cutoff],
    )}
    for run in runs:
        snapshots = snapshots_by_run[run["run_id"]]
        eligible = [(row, live_copies.get(row["manifest_hash"], 0)) for row in snapshots]
        recoverable = next((row for row, count in eligible if count), None)
        replicated = next(((row, count) for row, count in eligible if count and row["durable_at"] is not None), None)
        row = None if replicated is None else replicated[0]
        run["recoverable_checkpoint_done"] = 0 if recoverable is None else recoverable["done"]
        run["replicated_checkpoint_done"] = 0 if row is None else row["done"]
        run["replicated_checkpoint_hash"] = None if row is None else row["manifest_hash"]
        run["checkpoint_replicas"] = 0 if replicated is None else replicated[1]
        run["last_replicated_at"] = None if row is None else row["durable_at"]


# A restarted agent must prove its retained disk content before its old claims count as verified.
def revalidation(connection: sqlite3.Connection, node: str, now: float) -> dict[str, Any] | None:
    """Return one useful retained-content validation task without scanning the whole queue."""

    while True:
        row=connection.execute(
            "SELECT validation.* FROM node_revalidation validation "
            "WHERE validation.node_name=? ORDER BY CASE WHEN validation.kind='artifact' "
            "AND validation.digest IN (SELECT child.artifact_hash FROM distributed_tiles tile "
            "JOIN runs parent ON parent.run_id=tile.parent_run_id "
            "JOIN runs child ON child.run_id=tile.child_run_id "
            "WHERE parent.state='waiting') "
            "THEN 0 ELSE 1 END,validation.created,validation.kind,validation.digest LIMIT 1",
            (node,)).fetchone()
        if row is None:
            return None
        if row["kind"]=="checkpoint":
            snapshot=connection.execute("SELECT * FROM checkpoints WHERE manifest_hash=? AND retired_at IS NULL",(row["digest"],)).fetchone()
            if snapshot is not None:
                candidate=task(connection,snapshot,now)
                candidate.update(kind="revalidate_checkpoint",sources=[],specification=json.loads(connection.execute("SELECT specification FROM runs WHERE run_id=?",(snapshot["run_id"],)).fetchone()[0]),max_checkpoint_bytes=int(setting(connection,"max_checkpoint_bytes")))
                return candidate
        else:
            artifact=connection.execute("SELECT artifact_hash,size FROM artifacts WHERE artifact_hash=?",(row["digest"],)).fetchone()
            if artifact is not None:
                return {"kind":"revalidate_artifact",**dict(artifact)}
        drop_unverified(connection, node, row["kind"], row["digest"])


def revalidation_batch(connection: sqlite3.Connection, node: str, limit: int = 512) -> list[dict[str, Any]]:
    """Return a bounded retained inventory with every expected member and size."""
    rows = connection.execute(
        "SELECT validation.* FROM node_revalidation validation WHERE validation.node_name=? "
        "ORDER BY CASE WHEN validation.kind='artifact' AND validation.digest IN ("
        "SELECT child.artifact_hash FROM distributed_tiles tile JOIN runs parent ON parent.run_id=tile.parent_run_id "
        "JOIN runs child ON child.run_id=tile.child_run_id WHERE parent.state='waiting') "
        "THEN 0 ELSE 1 END,"
        "validation.created,validation.kind,validation.digest LIMIT ?", (node, limit),
    ).fetchall()
    result = []
    for row in rows:
        if row["kind"] == "artifact":
            artifact = connection.execute(
                "SELECT size FROM artifacts WHERE artifact_hash=?", (row["digest"],)
            ).fetchone()
            if artifact is not None and artifact["size"] is not None:
                result.append({"kind": "artifact", "digest": row["digest"],
                               "members": [{"digest": row["digest"],
                                            "size": artifact["size"]}]})
                continue
        else:
            snapshot = connection.execute(
                "SELECT manifest FROM checkpoints WHERE manifest_hash=? AND retired_at IS NULL",
                (row["digest"],),
            ).fetchone()
            if snapshot is not None:
                manifest = json.loads(snapshot["manifest"])
                members = [{"digest": row["digest"],
                            "size": len(canonical_json(manifest))}]
                members.extend({"digest": item["sha256"], "size": item["size"]}
                               for item in manifest["files"])
                result.append({"kind": "checkpoint", "digest": row["digest"],
                               "members": members})
                continue
        drop_unverified(connection, node, row["kind"], row["digest"])
    return result


def drop_unverified(connection: sqlite3.Connection, node: str, kind: str, digest: str) -> None:
    """End node's pending check of digest; a claim it did not re-prove is forgotten with it.

    A copy proved in the meantime was re-recorded as verified and stays.
    """

    connection.execute("DELETE FROM node_revalidation WHERE node_name=? AND kind=? AND digest=?", (node, kind, digest))
    if kind == "artifact":
        connection.execute("DELETE FROM replicas WHERE node_name=? AND artifact_hash=? AND verified=0", (node, digest))
    else:
        connection.execute("DELETE FROM checkpoint_replicas WHERE node_name=? AND manifest_hash=? AND verified=0",
                           (node, digest))
