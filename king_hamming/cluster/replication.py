"""Fenced, priority-ordered transfers of immutable cluster artifacts."""

from __future__ import annotations

import sqlite3
import uuid

import topology


SCHEMA = """
CREATE TABLE IF NOT EXISTS replica_transfers (
    artifact_hash TEXT PRIMARY KEY REFERENCES artifacts(artifact_hash),
    node_name TEXT NOT NULL REFERENCES nodes(node_name),
    session_id TEXT NOT NULL,
    token TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS replica_transfers_node ON replica_transfers(node_name);
"""

TRANSFER_SECONDS = 90.0


def initialize(connection: sqlite3.Connection) -> None:
    """Add transfer reservations without changing retained replica records."""

    connection.executescript(SCHEMA)


def assign(connection: sqlite3.Connection, node: sqlite3.Row, now: float,
           lease_seconds: float, grace_seconds: float,
           disk_floor_bytes: int = 0) -> dict | None:
    """Reserve one needed transfer; unblock active DP tiles before background copies.

    At most one node may fetch an artifact at a time. The leader's surrounding
    BEGIN IMMEDIATE transaction makes selection and reservation atomic.
    """

    connection.execute(
        "DELETE FROM replica_transfers WHERE expires<=? OR NOT EXISTS "
        "(SELECT 1 FROM nodes n WHERE n.node_name=replica_transfers.node_name "
        "AND n.session_id=replica_transfers.session_id AND n.last_heartbeat>?)",
        (now, now - lease_seconds),
    )
    # Only the two waiting DP roots need urgent second copies. Starting from
    # their children avoids evaluating JSON and replica counts for every old,
    # retired artifact in the database on each worker poll.
    row = connection.execute(
        "SELECT a.artifact_hash,a.size,a.target_replicas FROM runs parent "
        "JOIN runs child ON child.parent_run_id=parent.run_id AND child.state='complete' "
        "JOIN artifacts a ON a.artifact_hash=child.artifact_hash "
        "WHERE parent.state='waiting' "
        "AND json_extract(parent.specification,'$.program')='dp_distributed' "
        "AND NOT EXISTS (SELECT 1 FROM replicas own WHERE own.artifact_hash=a.artifact_hash "
        "AND own.node_name=?) "
        "AND NOT EXISTS (SELECT 1 FROM replica_transfers t WHERE t.artifact_hash=a.artifact_hash) "
        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)=1 "
        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)<a.target_replicas "
        "ORDER BY a.created,a.artifact_hash LIMIT 1",
        (node["node_name"], now - lease_seconds, now - grace_seconds),
    ).fetchone()
    if row is None:
        # Background work starts from indexed replicas, never from the many
        # retained artifact rows whose bytes have intentionally been retired.
        row = connection.execute(
            "SELECT a.artifact_hash,a.size,a.target_replicas FROM replicas source "
            "JOIN nodes sender ON sender.node_name=source.node_name "
            "JOIN artifacts a ON a.artifact_hash=source.artifact_hash "
            "WHERE sender.last_heartbeat>? "
            "AND NOT EXISTS (SELECT 1 FROM replicas own WHERE own.artifact_hash=a.artifact_hash "
            "AND own.node_name=?) "
            "AND NOT EXISTS (SELECT 1 FROM replica_transfers t WHERE t.artifact_hash=a.artifact_hash) "
            "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
            "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)<a.target_replicas "
            "GROUP BY a.artifact_hash "
            "ORDER BY (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
            "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?),a.created LIMIT 1",
            (now - lease_seconds, node["node_name"], now - grace_seconds,
             now - lease_seconds),
        ).fetchone()
    if row is None:
        return None
    # An isolated Wi-Fi agent must not claim a copy that three healthy members
    # of the producer's private LAN could keep entirely on that faster LAN.
    # If that LAN loses capacity, cross-group replication becomes eligible again.
    source_groups = [record[0] for record in connection.execute(
        "SELECT DISTINCT n.private_group FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=? AND n.private_group<>'' AND n.last_heartbeat>?",
        (row["artifact_hash"], now - lease_seconds),
    )]
    if source_groups and node["private_group"] not in source_groups:
        for group in source_groups:
            available = connection.execute(
                "SELECT COUNT(*) FROM nodes WHERE private_group=? AND last_heartbeat>? "
                "AND (storage_free_bytes<0 OR storage_free_bytes>=?)",
                (group, now - lease_seconds, disk_floor_bytes),
            ).fetchone()[0]
            if available >= row["target_replicas"]:
                return None
    locations = topology.locations(connection, row["artifact_hash"],
                                   node["node_name"], now - lease_seconds)
    if not locations:
        return None
    token = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO replica_transfers(artifact_hash,node_name,session_id,token,created,expires) "
        "VALUES(?,?,?,?,?,?)",
        (row["artifact_hash"], node["node_name"], node["session_id"], token,
         now, now + TRANSFER_SECONDS),
    )
    return {"artifact_hash": row["artifact_hash"], "size": row["size"],
            "kind": "artifact", "locations": locations, "location": locations[0],
            "transfer_token": token}


def renew(connection: sqlite3.Connection, node: sqlite3.Row, digest: str,
          token: str, now: float) -> None:
    """Extend only the current owner's unexpired transfer reservation."""

    updated = connection.execute(
        "UPDATE replica_transfers SET expires=? WHERE artifact_hash=? AND node_name=? "
        "AND session_id=? AND token=? AND expires>?",
        (now + TRANSFER_SECONDS, digest, node["node_name"], node["session_id"], token, now),
    ).rowcount
    if updated != 1:
        raise PermissionError("replication assignment expired or changed")


def release(connection: sqlite3.Connection, node: sqlite3.Row, digest: str,
            token: str) -> None:
    """Release an abandoned transfer without touching any completed replica."""

    connection.execute(
        "DELETE FROM replica_transfers WHERE artifact_hash=? AND node_name=? "
        "AND session_id=? AND token=?",
        (digest, node["node_name"], node["session_id"], token),
    )
