"""Conservative snapshot retirement and shared-blob garbage collection planning."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from recovery import setting, sources

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoint_pins (
    lease_token TEXT PRIMARY KEY,
    manifest_hash TEXT NOT NULL REFERENCES checkpoints(manifest_hash)
);
CREATE TABLE IF NOT EXISTS checkpoint_garbage (
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    blob_hash TEXT NOT NULL,
    PRIMARY KEY(node_name, blob_hash)
);
CREATE TABLE IF NOT EXISTS artifact_trim (
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    artifact_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created REAL NOT NULL,
    PRIMARY KEY(node_name, artifact_hash)
);
"""

# A node that has been silent this long (rather than the 60 s lease window) is presumed gone for
# good, and only then are its copies replaced. Shorter outages end with the node's copies intact.
DEFAULT_REPLICA_GRACE_SECONDS = 600.0
# Finished fields keep their tiles this long, so the result can be collected and rechecked.
DEFAULT_TILE_RETENTION_SECONDS = 6 * 3600.0
# A copy younger than this is never trimmed, so replication and trimming cannot chase each other.
MIN_TRIM_AGE_SECONDS = 600.0
TRIM_SCAN_SECONDS = 300.0
TRIM_BATCH = 128
TRIM_SCAN_LIMIT = 2000
_last_trim_scan: dict[str, float] = {}


# Preserve historical manifests while migrating the lifetime state of their bulk data.
def initialize(connection: sqlite3.Connection, keep: int) -> None:
    """Initialize connection's schema and retain at least keep replicated snapshots per run."""

    if type(keep) is not int or keep < 2:
        raise ValueError("checkpoint retention must keep at least two replicated snapshots")

    connection.executescript(SCHEMA)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(checkpoints)")}

    if "retired_at" not in columns:
        connection.execute("ALTER TABLE checkpoints ADD COLUMN retired_at REAL")

    connection.execute(
        "INSERT INTO settings(key,value) VALUES('checkpoint_keep',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(keep),),
    )
    # Operator-tunable (UPDATE settings SET value=... WHERE key=...); an existing value is kept.
    for key, value in (("replica_grace_seconds", DEFAULT_REPLICA_GRACE_SECONDS),
                       ("tile_retention_seconds", DEFAULT_TILE_RETENTION_SECONDS)):
        connection.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, str(value)))


def number(connection: sqlite3.Connection, key: str, default: float) -> float:
    """Return numeric setting key, or default when it is missing or unreadable."""

    row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    try:
        return float(row[0]) if row is not None else default
    except (TypeError, ValueError):
        return default


# Include the manifest object itself in the reference set of each snapshot.
def members(snapshot: sqlite3.Row) -> set[str]:
    """Return every content hash referenced by snapshot, including its manifest."""

    return {snapshot["manifest_hash"], *(record["sha256"] for record in json.loads(snapshot["manifest"])["files"])}


# Pins last only while the requesting computation lease remains valid.
def pinned(connection: sqlite3.Connection, now: float) -> set[str]:
    """Return snapshots selected by active restores at now; discard expired pins."""

    connection.execute(
        "DELETE FROM checkpoint_pins WHERE lease_token NOT IN "
        "(SELECT lease_token FROM runs WHERE state='running' AND lease_expires>?)", (now,),
    )
    return {row[0] for row in connection.execute("SELECT manifest_hash FROM checkpoint_pins")}


# Retire old state only behind multiple currently healthy independently copied successors.
def retire_old(connection: sqlite3.Connection, now: float) -> int:
    """Retire redundant snapshots in connection; return the count, retaining history and pins."""

    keep = int(setting(connection, "checkpoint_keep"))
    pins = pinned(connection, now)
    retired = 0
    runs = connection.execute("SELECT DISTINCT run_id FROM checkpoints WHERE retired_at IS NULL").fetchall()

    for run in runs:
        rows = connection.execute(
            "SELECT * FROM checkpoints WHERE run_id=? AND retired_at IS NULL "
            "ORDER BY cursor DESC, created DESC, manifest_hash", (run["run_id"],),
        ).fetchall()
        stable = 0
        stable_cursors: set[int] = set()

        for row in rows:
            if stable < keep:
                if row["cursor"] not in stable_cursors and len(sources(connection, row["manifest_hash"], now)) >= 2:
                    stable_cursors.add(row["cursor"])
                    stable += 1
                continue

            if row["manifest_hash"] in pins:
                continue

            holders = connection.execute(
                "SELECT node_name FROM checkpoint_replicas WHERE manifest_hash=?", (row["manifest_hash"],),
            ).fetchall()

            for holder in holders:
                connection.executemany(
                    "INSERT OR IGNORE INTO checkpoint_garbage(node_name,blob_hash) VALUES(?,?)",
                    [(holder["node_name"], digest) for digest in members(row)],
                )

            connection.execute("UPDATE checkpoints SET retired_at=? WHERE manifest_hash=?", (now, row["manifest_hash"]))
            connection.execute("DELETE FROM checkpoint_replicas WHERE manifest_hash=?", (row["manifest_hash"],))
            retired += 1

    return retired


# Surplus copies go first from the machines with the least free disk, never below the target.
def excess_plan(connection: sqlite3.Connection, node: str, now: float, limit: int) -> list[str]:
    """Choose up to limit artifacts for node to drop because enough healthy machines hold them.

    A copy is surplus when more than target_replicas healthy nodes (heartbeat within
    the lease window) hold verified copies of it; unverified copies neither count nor
    go. Holders are ranked by free disk, least first, and the first surplus-many are
    the ones to drop, so at least target copies always remain on healthy machines. Each chosen replica is removed from the index immediately,
    so no reader is sent to it, and queued in artifact_trim for the agent to delete.
    """

    healthy = now - setting(connection, "lease_seconds")
    # Rank every healthy holder of each artifact this node holds by free disk, least first; this
    # node drops its copy exactly when its rank is within the surplus. One query, so the limit
    # applies to copies this node is actually chosen to drop, however far down the table they are.
    chosen = [row[0] for row in connection.execute(
        "WITH mine AS (SELECT artifact_hash FROM replicas WHERE node_name=? AND created<? AND verified=1), "
        "ranked AS (SELECT x.artifact_hash,x.node_name,"
        "ROW_NUMBER() OVER (PARTITION BY x.artifact_hash ORDER BY n.storage_free_bytes,n.node_name) AS place,"
        "COUNT(*) OVER (PARTITION BY x.artifact_hash) AS holders "
        "FROM replicas x JOIN nodes n ON n.node_name=x.node_name "
        "WHERE n.last_heartbeat>? AND x.verified=1 AND x.artifact_hash IN (SELECT artifact_hash FROM mine)) "
        "SELECT ranked.artifact_hash FROM ranked JOIN artifacts a USING(artifact_hash) "
        "WHERE ranked.node_name=? AND ranked.place<=ranked.holders-a.target_replicas LIMIT ?",
        (node, now - MIN_TRIM_AGE_SECONDS, healthy, node, limit))]
    for digest in chosen:
        queue_trim(connection, node, digest, "excess", now)
    return chosen


def queue_trim(connection: sqlite3.Connection, node: str, digest: str, reason: str, now: float) -> None:
    """Forget node's copy of digest and queue its blob for deletion on that node.

    A pending re-check of that copy (the node restarted and is revalidating its disk) is
    cancelled too: otherwise the check could find the file before the deletion ran and record
    a verified copy of a file that was then deleted, which replication kept failing to fetch.
    """

    connection.execute("DELETE FROM replicas WHERE artifact_hash=? AND node_name=?", (digest, node))
    connection.execute("DELETE FROM node_revalidation WHERE node_name=? AND kind='artifact' AND digest=?",
                       (node, digest))
    connection.execute("INSERT OR IGNORE INTO artifact_trim(node_name,artifact_hash,reason,created) VALUES(?,?,?,?)",
                       (node, digest, reason, now))


# Shared objects remain protected by any retained checkpoint or final artifact.
def collect_plan(connection: sqlite3.Connection, node: str, now: float) -> dict[str, Any]:
    """Return bounded deletable hashes for node; caller holds that worker's storage transaction lock."""

    retired = retire_old(connection, now)
    # A replacement agent's disk inventory is read under its local storage
    # transaction. Do not race that proof with deletion from the same disk.
    proving = connection.execute(
        "SELECT COUNT(*) FROM node_revalidation WHERE node_name=?", (node,)
    ).fetchone()[0]
    if proving:
        return {"blob_hashes": [], "retired": retired,
                "checkpoint_keep": int(setting(connection, "checkpoint_keep")),
                "deferred": "storage revalidation", "revalidation_pending": proving}
    deletable: list[str] = []
    garbage = [row[0] for row in connection.execute(
        "SELECT blob_hash FROM checkpoint_garbage WHERE node_name=?", (node,))]
    pinned_members: set[str] = set()
    if garbage:
        protected = {row[0] for row in connection.execute("SELECT artifact_hash FROM artifacts")}
        for row in connection.execute("SELECT * FROM checkpoints WHERE retired_at IS NULL"):
            pinned_members.update(members(row))
        protected |= pinned_members
        for digest in garbage:
            if digest not in protected:
                deletable.append(digest)
            if len(deletable) == 128:
                break

    # Artifact copies the leader has already dropped from its index (finished fields, surplus
    # copies). Not in `protected`: that set keeps artifacts, which these still are, elsewhere.
    room = TRIM_BATCH - len(deletable)
    queued = [row["artifact_hash"] for row in connection.execute(
        "SELECT artifact_hash FROM artifact_trim WHERE node_name=? ORDER BY created,artifact_hash LIMIT ?",
        (node, TRIM_BATCH))]
    # Scanning for surplus copies costs a pass over this node's replicas, so it queues a big batch
    # at once and later plans just read the queue. It runs only when the queue has run dry.
    if len(queued) < room and now - _last_trim_scan.get(node, 0.0) >= TRIM_SCAN_SECONDS:
        found = excess_plan(connection, node, now, TRIM_SCAN_LIMIT)
        _last_trim_scan[node] = 0.0 if len(found) >= TRIM_SCAN_LIMIT else now
        queued = [row["artifact_hash"] for row in connection.execute(
            "SELECT artifact_hash FROM artifact_trim WHERE node_name=? ORDER BY created,artifact_hash LIMIT ?",
            (node, TRIM_BATCH))]
    if queued and not garbage:
        for row in connection.execute("SELECT * FROM checkpoints WHERE retired_at IS NULL"):
            pinned_members.update(members(row))
    # A blob that is also a retained checkpoint member must stay: cancel its deletion.
    kept = [digest for digest in queued if digest in pinned_members]
    connection.executemany("DELETE FROM artifact_trim WHERE node_name=? AND artifact_hash=?",
                           [(node, digest) for digest in kept])
    deletable.extend([digest for digest in queued if digest not in pinned_members][:max(0, room)])

    return {"blob_hashes": deletable, "retired": retired, "checkpoint_keep": int(setting(connection, "checkpoint_keep"))}
