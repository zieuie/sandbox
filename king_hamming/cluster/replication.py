"""Fenced, priority-ordered transfers of immutable cluster artifacts."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
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
# Copies are pushed when they are needed, so a node whose full scan found nothing
# waits this long before scanning again; each scan costs the leader about 0.5 s.
IDLE_SCAN_SECONDS = 15.0
_idle_until: dict[str, float] = {}


def scan_allowed(node_name: str, now: float) -> bool:
    """Return whether node_name may run the full candidate scan now."""

    until = _idle_until.get(node_name)
    return until is None or not 0 <= until - now <= IDLE_SCAN_SECONDS


def scan_found_nothing(node_name: str, now: float) -> None:
    """Hold node_name's next full scan for IDLE_SCAN_SECONDS."""

    _idle_until[node_name] = now + IDLE_SCAN_SECONDS


def initialize(connection: sqlite3.Connection) -> None:
    """Add transfer reservations without changing retained replica records."""

    connection.executescript(SCHEMA)


# Finding artifacts that need copies means counting live replicas across the replicas
# table, about half a second of leader CPU. Every polling node used to run that scan,
# and a node that found work polled again at once, so the leader spent nearly all of one
# core on it (measured: 90% of its CPU). One scan now serves every node: it lists up to
# CANDIDATE_LIMIT artifacts, urgent ones first, and each poll walks that list with cheap
# indexed checks. A new copy's next hop is also pushed at completion (push_next_copy), so
# the list is only the backstop for what a push missed.
CANDIDATE_LIMIT = 200
CANDIDATE_CACHE_SECONDS = 30.0
_candidates: dict[str, tuple[float, list[dict]]] = {}
_candidate_lock = threading.Lock()


def scan_candidates(connection: sqlite3.Connection, now: float, lease_seconds: float,
                    grace_seconds: float) -> list[dict]:
    """Return up to CANDIDATE_LIMIT artifacts short of copies, waiting roots' urgent ones first."""

    # Only the waiting DP roots need urgent second copies. Starting from their children
    # avoids evaluating replica counts for every old, retired artifact on each scan.
    rows = connection.execute(
        "SELECT a.artifact_hash,a.size,a.target_replicas FROM runs parent "
        "JOIN runs child ON child.parent_run_id=parent.run_id AND child.state='complete' "
        "JOIN artifacts a ON a.artifact_hash=child.artifact_hash "
        "WHERE parent.state='waiting' "
        "AND json_extract(parent.specification,'$.program')='dp_distributed' "
        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)=1 "
        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)<a.target_replicas "
        "ORDER BY a.created,a.artifact_hash LIMIT ?",
        (now - lease_seconds, now - grace_seconds, CANDIDATE_LIMIT),
    ).fetchall()
    found = [dict(row) for row in rows]
    if len(found) < CANDIDATE_LIMIT:
        # Background work starts from indexed replicas, never from the many retained
        # artifact rows whose bytes have intentionally been retired.
        seen = {entry["artifact_hash"] for entry in found}
        for row in connection.execute(
                "SELECT a.artifact_hash,a.size,a.target_replicas FROM replicas source "
                "JOIN nodes sender ON sender.node_name=source.node_name "
                "JOIN artifacts a ON a.artifact_hash=source.artifact_hash "
                "WHERE sender.last_heartbeat>? "
                "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
                "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)<a.target_replicas "
                "GROUP BY a.artifact_hash "
                "ORDER BY (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
                "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?),a.created LIMIT ?",
                (now - lease_seconds, now - grace_seconds, now - lease_seconds, CANDIDATE_LIMIT)):
            if row["artifact_hash"] not in seen:
                found.append(dict(row))
                if len(found) >= CANDIDATE_LIMIT:
                    break
    return found


def cached_candidates(connection: sqlite3.Connection, now: float, lease_seconds: float,
                      grace_seconds: float) -> list[dict]:
    """Return the shared candidate list, rescanning at most every CANDIDATE_CACHE_SECONDS."""

    key = next((row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), "")
    with _candidate_lock:
        taken, rows = _candidates.get(key, (float("-inf"), []))
        if not 0 <= now - taken < CANDIDATE_CACHE_SECONDS:
            rows = scan_candidates(connection, now, lease_seconds, grace_seconds)
            _candidates[key] = (now, rows)
        return rows


def select_candidate(connection: sqlite3.Connection, node: sqlite3.Row, now: float,
                     lease_seconds: float, grace_seconds: float,
                     disk_floor_bytes: int = 0) -> dict | None:
    """Find a useful copy for node from the shared candidate list; no writer lock is required."""

    row = None
    for entry in cached_candidates(connection, now, lease_seconds, grace_seconds):
        digest = entry["artifact_hash"]
        if connection.execute("SELECT 1 FROM replicas WHERE artifact_hash=? AND node_name=?",
                              (digest, node["node_name"])).fetchone():
            continue
        if connection.execute("SELECT 1 FROM replica_transfers WHERE artifact_hash=? AND expires>?",
                              (digest, now)).fetchone():
            continue
        live = connection.execute(
            "SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
            "WHERE r.artifact_hash=? AND n.last_heartbeat>?", (digest, now - grace_seconds)).fetchone()[0]
        if live >= entry["target_replicas"]:
            continue
        row = entry
        break
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
    return {"artifact_hash": row["artifact_hash"], "size": row["size"]}


def reserve_candidate(connection: sqlite3.Connection, node: sqlite3.Row,
                      candidate: dict, now: float, lease_seconds: float,
                      grace_seconds: float, disk_floor_bytes: int = 0) -> dict | None:
    """Recheck a reader's suggestion and atomically claim only that one artifact."""

    digest = candidate["artifact_hash"]
    connection.execute(
        "DELETE FROM replica_transfers WHERE artifact_hash=? AND (expires<=? OR NOT EXISTS "
        "(SELECT 1 FROM nodes n WHERE n.node_name=replica_transfers.node_name "
        "AND n.session_id=replica_transfers.session_id AND n.last_heartbeat>?))",
        (digest, now, now - lease_seconds),
    )
    row = connection.execute(
        "SELECT a.size,a.target_replicas FROM artifacts a WHERE a.artifact_hash=? "
        "AND NOT EXISTS (SELECT 1 FROM replicas own WHERE own.artifact_hash=a.artifact_hash "
        "AND own.node_name=?) "
        "AND NOT EXISTS (SELECT 1 FROM replica_transfers t WHERE t.artifact_hash=a.artifact_hash) "
        "AND (SELECT COUNT(*) FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=a.artifact_hash AND n.last_heartbeat>?)<a.target_replicas",
        (digest, node["node_name"], now - grace_seconds),
    ).fetchone()
    if row is None:
        return None
    source_groups = [record[0] for record in connection.execute(
        "SELECT DISTINCT n.private_group FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=? AND n.private_group<>'' AND n.last_heartbeat>?",
        (digest, now - lease_seconds),
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
    locations = topology.locations(connection, digest, node["node_name"], now - lease_seconds)
    if not locations:
        return None
    token = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO replica_transfers(artifact_hash,node_name,session_id,token,created,expires) "
        "VALUES(?,?,?,?,?,?)",
        (digest, node["node_name"], node["session_id"], token, now, now + TRANSFER_SECONDS),
    )
    return {"artifact_hash": digest, "size": row["size"], "kind": "artifact",
            "locations": locations, "location": locations[0], "transfer_token": token}


# Copies used to wait until an idle agent found them by scanning, and every agent's
# scan returned the same oldest artifact: second copies trailed completion by about
# five minutes. The leader now names the next copy's node when a copy lands, and
# that node's next poll finds it with one indexed lookup.
def push_next_copy(connection: sqlite3.Connection, digest: str, now: float,
                   lease_seconds: float, grace_seconds: float,
                   disk_floor_bytes: int = 0) -> str | None:
    """Reserve digest's next needed copy for one chosen live node; return that node or None.

    Callers hold the writer transaction. A copy already reserved, enough live copies,
    or no live source or eligible node leaves things to the background scan.
    """

    artifact = connection.execute(
        "SELECT target_replicas FROM artifacts WHERE artifact_hash=?", (digest,)).fetchone()
    if artifact is None:
        return None
    connection.execute(
        "DELETE FROM replica_transfers WHERE artifact_hash=? AND (expires<=? OR NOT EXISTS "
        "(SELECT 1 FROM nodes n WHERE n.node_name=replica_transfers.node_name "
        "AND n.session_id=replica_transfers.session_id AND n.last_heartbeat>?))",
        (digest, now, now - lease_seconds),
    )
    if connection.execute("SELECT 1 FROM replica_transfers WHERE artifact_hash=?", (digest,)).fetchone():
        return None
    holders = {row[0]: (row[1], row[2]) for row in connection.execute(
        "SELECT r.node_name,n.last_heartbeat,n.private_group FROM replicas r "
        "JOIN nodes n USING(node_name) WHERE r.artifact_hash=?", (digest,))}
    if sum(heartbeat > now - grace_seconds for heartbeat, _ in holders.values()) >= artifact[0]:
        return None
    source_groups = {group for heartbeat, group in holders.values() if heartbeat > now - lease_seconds}
    if not source_groups:
        return None
    pending = dict(connection.execute(
        "SELECT node_name,COUNT(*) FROM replica_transfers WHERE expires>? GROUP BY node_name", (now,)))
    candidates = [row for row in connection.execute(
        "SELECT node_name,session_id,private_group FROM nodes n WHERE last_heartbeat>? "
        "AND (storage_free_bytes IS NULL OR storage_free_bytes<0 OR storage_free_bytes>=?) "
        "AND NOT EXISTS (SELECT 1 FROM node_dispatch_pauses pause WHERE pause.node_name=n.node_name)",
        (now - lease_seconds, disk_floor_bytes)) if row[0] not in holders]
    if not candidates:
        return None
    # Keep copies on a producer's private LAN when it has room, as the scan does.
    shared = [row for row in candidates if row[2] and row[2] in source_groups]
    pool = shared or candidates
    name, session_id, _ = min(pool, key=lambda row: (
        pending.get(row[0], 0), hashlib.sha256((digest + row[0]).encode()).digest()))
    connection.execute(
        "INSERT INTO replica_transfers(artifact_hash,node_name,session_id,token,created,expires) "
        "VALUES(?,?,?,?,?,?)",
        (digest, name, session_id, str(uuid.uuid4()), now, now + TRANSFER_SECONDS),
    )
    return name


def pending_assignment(connection: sqlite3.Connection, node: sqlite3.Row, now: float,
                       lease_seconds: float) -> dict | None:
    """Return a copy already reserved for this node's session, in the scan's reply shape."""

    row = connection.execute(
        "SELECT t.artifact_hash,t.token,a.size FROM replica_transfers t "
        "JOIN artifacts a ON a.artifact_hash=t.artifact_hash "
        "WHERE t.node_name=? AND t.session_id=? AND t.expires>? "
        "AND NOT EXISTS (SELECT 1 FROM replicas own WHERE own.artifact_hash=t.artifact_hash "
        "AND own.node_name=t.node_name) ORDER BY t.created LIMIT 1",
        (node["node_name"], node["session_id"], now),
    ).fetchone()
    if row is None:
        return None
    locations = topology.locations(connection, row[0], node["node_name"], now - lease_seconds)
    if not locations:
        return None
    return {"artifact_hash": row[0], "size": row[2], "kind": "artifact",
            "locations": locations, "location": locations[0], "transfer_token": row[1]}


def assign(connection: sqlite3.Connection, node: sqlite3.Row, now: float,
           lease_seconds: float, grace_seconds: float,
           disk_floor_bytes: int = 0) -> dict | None:
    """Compatibility helper for callers already in one writer transaction."""

    candidate = select_candidate(connection, node, now, lease_seconds,
                                 grace_seconds, disk_floor_bytes)
    if candidate is None:
        return None
    return reserve_candidate(connection, node, candidate, now, lease_seconds,
                             grace_seconds, disk_floor_bytes)


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
