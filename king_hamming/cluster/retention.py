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
"""


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
    protected = {row[0] for row in connection.execute("SELECT artifact_hash FROM artifacts")}

    for row in connection.execute("SELECT * FROM checkpoints WHERE retired_at IS NULL"):
        protected.update(members(row))

    deletable: list[str] = []

    for row in connection.execute("SELECT blob_hash FROM checkpoint_garbage WHERE node_name=?", (node,)):
        if row["blob_hash"] not in protected:
            deletable.append(row["blob_hash"])

        if len(deletable) == 128:
            break

    return {"blob_hashes": deletable, "retired": retired, "checkpoint_keep": int(setting(connection, "checkpoint_keep"))}
