"""Durable DP tile DAG scheduling and bounded dependency descriptions for the leader."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import json
import math
import os
import sqlite3
import uuid
from typing import Any

from blob_store import valid_digest
import retention as retention_module
import topology
from common import calculation_id, canonical_json
from dp_solver.scheduling import dp_estimate
from dp_solver.tiles import BAND_KINDS, band_kind, dependencies, memory_bytes, tile

SCHEMA = """
CREATE TABLE IF NOT EXISTS distributed_tiles (
    parent_run_id TEXT NOT NULL REFERENCES runs(run_id),
    row INTEGER NOT NULL,
    column INTEGER NOT NULL,
    child_run_id TEXT REFERENCES runs(run_id),
    PRIMARY KEY(parent_run_id,row,column)
);
CREATE INDEX IF NOT EXISTS distributed_children ON distributed_tiles(child_run_id);
CREATE INDEX IF NOT EXISTS distributed_attempts ON runs(calculation_id,created);
CREATE TABLE IF NOT EXISTS distributed_tile_retries (
    parent_run_id TEXT NOT NULL,
    row INTEGER NOT NULL,
    column INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    next_retry REAL NOT NULL,
    PRIMARY KEY(parent_run_id,row,column)
);
CREATE TABLE IF NOT EXISTS tile_bands (
    packet_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    band_hash TEXT NOT NULL,
    PRIMARY KEY(packet_hash,kind)
);
"""

REUSE_BATCH = 32
MAX_BAND_BYTES = 2 * 1024**3
TILE_RETRY_LIMIT = 3
TILE_RETRY_BASE_SECONDS = 30
TILE_RETRY_MAX_SECONDS = 300
# A scheduling pass looks only at a root's open frontier (see advance), so it costs the
# same however large the grid is until the root has FULL_SCAN_TILES tiles; beyond that
# its frontier queries are themselves spaced out, tiles/FULL_SCAN_TILES seconds apart up
# to MAX_SCAN_INTERVAL. Ready tiles are already queued, so a few seconds' delay idles
# nothing. Grid-wide work (progress totals, the reconstruction trigger, recomputing lost
# tiles) runs every REFRESH_SECONDS, or every pass once no tile is left to create.
FULL_SCAN_TILES = 100_000
MAX_SCAN_INTERVAL = 30.0
TILE_CLEAR_MAX_FRACTION = 0.01   # most a pass may clear of a root's finished tiles (setting tile_clear_max_fraction)
TILE_CLEAR_MIN_TILES = 50        # ... but at least this many, so small roots can still repair themselves
REFRESH_SECONDS = 10.0
END_GAME_REFRESH_SECONDS = 3.0
REFRESH_TILES_PER_SECOND = 1000.0   # a grid of N tiles refreshes at most every N/1000 s
_last_refresh: dict[str, float] = {}
_last_scan: dict[str, float] = {}


# Store dependency ownership separately from ordinary run and lease history.
def initialize(connection: sqlite3.Connection) -> None:
    """Create the persistent tile DAG schema without replacing earlier results."""

    connection.executescript(SCHEMA)


# Validate and materialize one root's finite, bounded tile frontier.
def create(connection: sqlite3.Connection, run_id: str, specification: dict[str, Any]) -> None:
    """Initialize run_id's tile grid and waiting state using checked specification limits."""

    arguments = specification["arguments"]
    estimate = dp_estimate(specification)
    side = int(arguments.get("tile_side", 4096))
    limit = int(arguments.get("max_tiles", 10000))
    maximum = int(arguments.get("max_tile_bytes", 2*1024**3))
    if min(side, limit, maximum, int(arguments.get("threads", 1))) < 1:
        raise ValueError("invalid distributed tile controls")
    if estimate["raw_visits"] > int(arguments.get("max_visits", 5_000_000_000)):
        raise ValueError("distributed raw work exceeds max_visits")
    count = (estimate["budget"] + side - 1) // side
    if count**2 > limit:
        raise ValueError("distributed tile count exceeds max_tiles")
    candidates = {max(0,count-2),count-1}
    largest = max(memory_bytes(arguments["p"],tile(arguments["p"],arguments["r"],side,row,column),int(arguments.get("threads",1)))
                  for row in candidates for column in candidates)
    if largest > maximum:
        raise ValueError("distributed tile memory exceeds max_tile_bytes")
    connection.executemany("INSERT INTO distributed_tiles(parent_run_id,row,column) VALUES(?,?,?)",
                           [(run_id, row, column) for row in range(count) for column in range(count)])
    connection.execute("UPDATE runs SET state='waiting',progress_total=?,progress_units='cells',progress_phase='tiles' WHERE run_id=?",
                       (estimate["budget"]**2, run_id))


# Only live complete artifact replicas establish an available predecessor.
def artifact_record(connection: sqlite3.Connection, row: sqlite3.Row, now: float,
                    requester: str | None = None) -> dict[str, Any] | None:
    """Return row's artifact descriptor with live sources, or None if it is not complete."""

    if row["state"] != "complete" or not row["artifact_hash"]:
        return None
    deadline = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
    sources = topology.locations(connection, row["artifact_hash"], requester, now-deadline)
    size = connection.execute("SELECT size FROM artifacts WHERE artifact_hash=?", (row["artifact_hash"],)).fetchone()[0]
    return {"sha256": row["artifact_hash"], "size": size, "locations": sources}


# A band is addressed by the packet it was cut from, so reused tiles keep their bands for free.
def band_record(connection: sqlite3.Connection, packet_hash: str, kind: str, now: float,
                requester: str | None = None) -> dict[str, Any] | None:
    """Return kind's band descriptor for packet_hash with live sources, or None if none is available."""

    row = connection.execute("SELECT band_hash FROM tile_bands WHERE packet_hash=? AND kind=?",
                             (packet_hash, kind)).fetchone()
    if row is None:
        return None
    deadline = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
    sources = topology.locations(connection, row["band_hash"], requester, now-deadline)
    size = connection.execute("SELECT size FROM artifacts WHERE artifact_hash=?", (row["band_hash"],)).fetchone()
    if not sources or size is None or size[0] is None:
        return None
    return {"kind": kind, "sha256": row["band_hash"], "size": size[0], "locations": sources}


# The worker stored the band blobs on its own disk; the leader indexes them like any artifact.
def publish_bands(connection: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any], now: float) -> dict[str, Any]:
    """Index the edge bands a leased tile cut from its packet; return how many were registered.

    Bands are an optimization. Registering them never changes the tile's result,
    and a missing or damaged band simply sends a successor to the whole packet.
    """

    if json.loads(run["specification"])["program"] != "dp_tile":
        raise ValueError("only a tile task publishes bands")
    published = request["publish_bands"]
    packet = published["packet"]
    bands = published["bands"]
    if not valid_digest(packet) or not isinstance(bands, dict) or not 0 < len(bands) <= len(BAND_KINDS):
        raise ValueError("invalid band publication")
    for kind, entry in bands.items():
        if (kind not in BAND_KINDS or not isinstance(entry, dict) or not valid_digest(entry.get("sha256")) or
                type(entry.get("size")) is not int or not 0 < entry["size"] <= MAX_BAND_BYTES or
                not isinstance(entry.get("location"), str) or not 0 < len(entry["location"]) <= 512):
            raise ValueError("invalid band description")
    for kind, entry in bands.items():
        connection.execute("INSERT OR IGNORE INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,?,?)",
                           (entry["sha256"], now, entry["size"]))
        connection.execute("INSERT OR REPLACE INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,?)",
                           (entry["sha256"], run["node_name"], entry["location"], now))
        connection.execute("INSERT OR REPLACE INTO tile_bands(packet_hash,kind,band_hash) VALUES(?,?,?)",
                           (packet, kind, entry["sha256"]))
    return {"registered": len(bands)}


def child_specification(parent: sqlite3.Row, row: int, column: int) -> dict[str, Any]:
    """Build the exact child identity owned by this parent attempt."""
    arguments = json.loads(parent["specification"])["arguments"]
    result = {"program": "dp_tile", "arguments": {
        "parent_run_id": parent["run_id"], "p": arguments["p"], "r": arguments["r"],
        "row": row, "column": column,
        "tile_side": int(arguments.get("tile_side", 4096)),
        "threads": int(arguments.get("threads", 1)),
        "max_tile_bytes": int(arguments.get("max_tile_bytes", 2 * 1024**3)),
    }}
    # Present only when chosen, so format-1 tiles keep their earlier identities.
    for name in ("max_cpus", "tile_format"):
        if name in arguments:
            result["arguments"][name] = arguments[name]
    return result


def reuse_tiles(connection: sqlite3.Connection, parent: sqlite3.Row, now: float) -> int:
    """Import earlier exact attempt tiles only while two live replicas prove durability.

    A new complete child preserves the new attempt's lineage and references the
    same content-addressed blob. Queued children may be superseded atomically;
    already leased children are never revoked or given a competing writer.
    """
    if parent["from_scratch"]:
        return 0
    sources = connection.execute(
        "SELECT source.row,source.column,child.*,target.child_run_id AS target_child_id,"
        "target_child.state AS target_state "
        "FROM runs previous JOIN distributed_tiles source ON source.parent_run_id=previous.run_id "
        "JOIN runs child ON child.run_id=source.child_run_id "
        "JOIN distributed_tiles target ON target.parent_run_id=? "
        "AND target.row=source.row AND target.column=source.column "
        "LEFT JOIN runs target_child ON target_child.run_id=target.child_run_id "
        "WHERE previous.calculation_id=? AND previous.parent_run_id IS NULL "
        "AND previous.run_id<>? AND previous.created<=? AND child.state='complete' "
        "AND (target.child_run_id IS NULL OR target_child.state IN "
        "('queued','failed','cancelled')) "
        "ORDER BY source.row,source.column,previous.created DESC,child.finished DESC",
        (parent["run_id"], parent["calculation_id"], parent["run_id"], parent["created"]),
    ).fetchall()
    imported, seen = 0, set()
    for source in sources:
        key = (source["row"], source["column"])
        if key in seen:
            continue
        descriptor = artifact_record(connection, source, now)
        if descriptor is None or len(descriptor["locations"]) < 2:
            continue
        seen.add(key)
        if source["target_state"] == "queued":
            connection.execute(
                "UPDATE runs SET state='cancelled',finished=?,progress_phase='cancelled',"
                "progress_message='reused durable tile from earlier attempt' "
                "WHERE run_id=? AND state='queued'",
                (now, source["target_child_id"]),
            )
        specification = child_specification(parent, *key)
        target = tile(specification["arguments"]["p"], specification["arguments"]["r"],
                      specification["arguments"]["tile_side"], *key)
        cells = target.value_bytes // 8
        child = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO runs(run_id,calculation_id,specification,state,priority,"
            "from_scratch,created,started,finished,estimated_seconds,parent_run_id,"
            "progress_done,progress_total,progress_checkpoint_done,progress_phase,"
            "progress_units,progress_message,artifact_hash,artifact_location) "
            "VALUES(?,?,?,'complete',?,0,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (child, calculation_id(specification), canonical_json(specification).decode(),
             parent["priority"], now, now, now, parent["estimated_seconds"],
             parent["run_id"], cells, cells, cells, "complete", "cells",
             f"reused completed tile from {source['run_id']}",
             source["artifact_hash"], descriptor["locations"][0]),
        )
        connection.execute(
            "UPDATE distributed_tiles SET child_run_id=? "
            "WHERE parent_run_id=? AND row=? AND column=?",
            (child, parent["run_id"], *key),
        )
        imported += 1
        if imported >= REUSE_BATCH:
            break
    return imported


RETIRE_SCAN_SECONDS = 120
RETIRE_BATCH = 100
_ACTIVE_ROOT = "('waiting','queued','running','stopping','paused')"
_ACTIVE_ROOT_STATES = {"waiting", "queued", "running", "stopping", "paused"}


# Tile packets are only scaffolding for the final split: once a field's result is safely stored,
# they and their bands are dead weight, and each is held by several machines.
def retire_finished_tiles(connection: sqlite3.Connection, now: float, force: bool = False) -> int:
    """Queue deletion of the tiles of finished fields on every holder; return how many packets were retired.

    A packet is retired when every root that references it (attempts share packets through
    reuse_tiles) is either itself complete or is a failed or cancelled attempt of a field that
    has a complete root, and the completing root finished at least tile_retention_seconds ago
    with its result held on two live nodes. Any waiting, queued, running or paused root keeps
    its packets. Retired replicas leave the index at once, so nothing is sent to them, and the
    holders delete the blobs when they next collect garbage. tile_retention_seconds of -1
    keeps tiles forever.
    """

    retention = retention_module.number(connection, "tile_retention_seconds", retention_module.DEFAULT_TILE_RETENTION_SECONDS)
    if retention < 0:
        return 0
    last = retention_module.number(connection, "tile_retire_scanned", 0.0)
    if not force and now - last < RETIRE_SCAN_SECONDS:
        return 0
    connection.execute("INSERT INTO settings(key,value) VALUES('tile_retire_scanned',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),))
    lease = retention_module.setting(connection, "lease_seconds")
    packets = [row[0] for row in connection.execute(
        "WITH done AS (SELECT p.calculation_id AS calc FROM runs p WHERE p.parent_run_id IS NULL "
        "AND p.state='complete' AND p.artifact_hash IS NOT NULL AND p.finished<=? "
        "AND (SELECT COUNT(*) FROM replicas x JOIN nodes n USING(node_name) "
        "WHERE x.artifact_hash=p.artifact_hash AND x.verified=1 AND n.last_heartbeat>?)>=2) "
        "SELECT c.artifact_hash FROM runs c JOIN runs p ON p.run_id=c.parent_run_id "
        "WHERE c.artifact_hash IS NOT NULL AND c.state='complete' "
        "AND json_extract(c.specification,'$.program')='dp_tile' "
        "AND EXISTS (SELECT 1 FROM replicas h WHERE h.artifact_hash=c.artifact_hash) "
        "GROUP BY c.artifact_hash "
        f"HAVING SUM(p.state IN {_ACTIVE_ROOT})=0 "
        "AND SUM(p.calculation_id IN (SELECT calc FROM done))=COUNT(*) LIMIT ?",
        (now - retention, now - lease, RETIRE_BATCH))]
    for packet in packets:
        hashes = [packet] + [row[0] for row in connection.execute(
            "SELECT band_hash FROM tile_bands WHERE packet_hash=?", (packet,))]
        for digest in hashes:
            for (node,) in connection.execute("SELECT node_name FROM replicas WHERE artifact_hash=?", (digest,)).fetchall():
                retention_module.queue_trim(connection, node, digest, "finished field", now)
    if len(packets) >= RETIRE_BATCH:  # more are waiting: scan again on the next pass
        connection.execute("UPDATE settings SET value='0' WHERE key='tile_retire_scanned'")
    return len(packets)


def retire_orphan_bands(connection: sqlite3.Connection, now: float) -> int:
    """Queue deletion of band copies whose tile packet has already been retired; return how many.

    A one-off repair, run by hand (not by the scheduler: the check costs about a second on
    the live database). Until 2026-10-04 a restarted worker's pending revalidation could
    re-record a band copy that retire_finished_tiles had just queued for deletion; the file
    was then deleted, leaving a verified claim that replication tried to fetch forever (404).
    queue_trim now cancels such checks, and gc-done drops claims on deleted blobs. A band is
    swept only when its packet has no copies left and every root using the packet is
    finished, the same rule retire_finished_tiles applies to the packet itself.
    """

    claims = connection.execute(
        "SELECT h.node_name,h.artifact_hash,b.packet_hash FROM tile_bands b "
        "JOIN replicas h ON h.artifact_hash=b.band_hash "
        "WHERE NOT EXISTS (SELECT 1 FROM replicas p WHERE p.artifact_hash=b.packet_hash)").fetchall()
    if not claims:
        return 0
    # runs has no index on artifact_hash, so read each finished tile's roots in one pass.
    finished, active = set(), set()
    for packet, root_state in connection.execute(
            "SELECT c.artifact_hash,root.state FROM runs c JOIN runs root ON root.run_id=c.parent_run_id "
            "WHERE c.state='complete' AND c.artifact_hash IS NOT NULL "
            "AND json_extract(c.specification,'$.program')='dp_tile'"):
        finished.add(packet)
        if root_state in _ACTIVE_ROOT_STATES:
            active.add(packet)
    swept = 0
    for node, digest, packet in claims:
        if packet in finished and packet not in active:
            retention_module.queue_trim(connection, node, digest, "orphaned band", now)
            swept += 1
    return swept


# Durable means complete with at least two verified replicas on live nodes, read in one query.
def durable_tiles(connection: sqlite3.Connection, parent_run_id: str, now: float,
                  lease_seconds: float, copies: int = 2) -> set[tuple[int, int]]:
    """Return the (row, column) of parent's complete tiles with at least copies live replicas."""

    return {(row[0], row[1]) for row in connection.execute(
        "SELECT t.row,t.column FROM distributed_tiles t "
        "JOIN runs child ON child.run_id=t.child_run_id "
        "JOIN replicas replica ON replica.artifact_hash=child.artifact_hash AND replica.verified=1 "
        "JOIN nodes node ON node.node_name=replica.node_name "
        "WHERE t.parent_run_id=? AND child.state='complete' "
        "AND node.last_heartbeat>? GROUP BY t.row,t.column HAVING COUNT(*)>=?",
        (parent_run_id, now - lease_seconds, copies))}


# Waiting for a second copy before successors could start held every tile wave for
# the replication queue (about five minutes). A successor can read the one live copy;
# if a tile's only copies disappear, recompute_lost_tiles recomputes it instead.
DEFAULT_DEPENDENCY_REPLICAS = 1


def dependency_copies(connection: sqlite3.Connection) -> int:
    """Live copies a finished tile needs before successors may use it (setting dependency_replicas)."""

    return max(1, int(retention_module.number(connection, "dependency_replicas",
                                              DEFAULT_DEPENDENCY_REPLICAS)))


def recompute_lost_tiles(connection: sqlite3.Connection, parent_run_id: str, now: float) -> int:
    """Clear complete tiles whose every copy has been out of reach past the replica grace; return how many.

    A brief Wi-Fi drop or agent restart stays within the grace and keeps the tile.
    A cleared tile is queued again once its own predecessors are available.
    """

    grace = retention_module.number(connection, "replica_grace_seconds",
                                    retention_module.DEFAULT_REPLICA_GRACE_SECONDS)
    # A restarted agent's copy is unverified until it re-checks its disk (minutes to hours for a big
    # store), and an unverified copy on a live node is not a lost one. Leaders once deleted those
    # claims instead, and a fleet-wide worker upgrade cleared 87% of a root's finished tiles.
    lost = connection.execute(
        "SELECT t.row,t.column,t.child_run_id FROM distributed_tiles t "
        "JOIN runs child ON child.run_id=t.child_run_id "
        "WHERE t.parent_run_id=? AND child.state='complete' AND child.finished<? "
        "AND NOT EXISTS (SELECT 1 FROM replicas r JOIN nodes n USING(node_name) "
        "WHERE r.artifact_hash=child.artifact_hash AND n.last_heartbeat>?)",
        (parent_run_id, now - grace, now - grace)).fetchall()
    # Circuit breaker. Clearing finished work is the one destructive step the scheduler takes on
    # its own, and it rests on bookkeeping (replica rows, heartbeats) that can be wrong in bulk.
    # A pass that would clear more than a small share of a root's finished tiles clears nothing
    # and says so (a setting the dashboard shows); an operator who knows the copies really are
    # gone raises tile_clear_max_fraction.
    finished = connection.execute(
        "SELECT COUNT(*) FROM distributed_tiles t JOIN runs child ON child.run_id=t.child_run_id "
        "WHERE t.parent_run_id=? AND child.state='complete'", (parent_run_id,)).fetchone()[0]
    fraction = retention_module.number(connection, "tile_clear_max_fraction", TILE_CLEAR_MAX_FRACTION)
    limit = max(TILE_CLEAR_MIN_TILES, fraction * finished)
    hold_key = f"tile_clear_hold:{parent_run_id}"
    if len(lost) > limit:
        held = connection.execute("SELECT value FROM settings WHERE key=?", (hold_key,)).fetchone()
        previous = json.loads(held[0]).get("would_clear") if held else None
        if previous != len(lost):
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (hold_key, json.dumps(
                {"time": now, "would_clear": len(lost), "finished": finished, "limit": int(limit)})))
            print(f"distributed: refusing to clear {len(lost)} of {finished} finished tiles of {parent_run_id[:8]} "
                  f"(limit {int(limit)}); recomputing is paused until the copies return or "
                  f"tile_clear_max_fraction is raised", flush=True)
        return 0
    connection.execute("DELETE FROM settings WHERE key=?", (hold_key,))
    for row, column, child in lost:
        connection.execute("UPDATE distributed_tiles SET child_run_id=NULL WHERE parent_run_id=? AND row=? "
                           "AND column=? AND child_run_id=?", (parent_run_id, row, column, child))
    return len(lost)


def restore_cleared_tiles(connection: sqlite3.Connection, parent_run_id: str) -> int:
    """Point cleared tiles back at their finished child when its artifact still has a copy.

    Repairs the damage described in recompute_lost_tiles: the child runs completed, their blobs
    are on disk and registered again, but the tile row was cleared. Only tiles still without a
    child are touched; the newest finished child with a replica (verified or not) wins. Returns how many tiles were restored. Run inside a write transaction.
    """

    empty = {(row[0], row[1]) for row in connection.execute(
        "SELECT row,column FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NULL",
        (parent_run_id,))}
    if not empty:
        return 0
    best: dict[tuple[int, int], tuple[float, str]] = {}
    for child in connection.execute(
            "SELECT c.run_id,c.finished,c.specification FROM runs c WHERE c.parent_run_id=? AND c.state='complete' "
            "AND c.artifact_hash IS NOT NULL AND EXISTS (SELECT 1 FROM replicas r WHERE r.artifact_hash=c.artifact_hash)",
            (parent_run_id,)):
        arguments = json.loads(child["specification"]).get("arguments", {})
        key = (arguments.get("row"), arguments.get("column"))
        if key in empty and (key not in best or (child["finished"] or 0) > best[key][0]):
            best[key] = (child["finished"] or 0, child["run_id"])
    for (row, column), (_, run_id) in best.items():
        connection.execute("UPDATE distributed_tiles SET child_run_id=? WHERE parent_run_id=? AND row=? AND column=? "
                           "AND child_run_id IS NULL", (run_id, parent_run_id, row, column))
    return len(best)


def durable_among(connection: sqlite3.Connection, parent_run_id: str, coordinates, now: float,
                  lease_seconds: float, copies: int = 2) -> set[tuple[int, int]]:
    """Return the given (row, column) tiles of parent that are complete with enough live replicas."""

    found: set[tuple[int, int]] = set()
    wanted = list(dict.fromkeys(coordinates))
    for start in range(0, len(wanted), 300):
        chunk = wanted[start:start + 300]
        values = ",".join("(?,?)" for _ in chunk)
        arguments = [value for key in chunk for value in key] + [parent_run_id, now - lease_seconds, copies]
        found.update((row[0], row[1]) for row in connection.execute(
            f"WITH want(row,column) AS (VALUES {values}) "
            # CROSS JOIN fixes the order: from the few wanted coordinates outward. Left to
            # itself the planner started from the 200,000-row replicas table (about 1 s).
            "SELECT t.row,t.column FROM want w "
            "CROSS JOIN distributed_tiles t ON t.row=w.row AND t.column=w.column AND t.parent_run_id=? "
            "CROSS JOIN runs child ON child.run_id=t.child_run_id AND child.state='complete' "
            "CROSS JOIN replicas replica ON replica.artifact_hash=child.artifact_hash AND replica.verified=1 "
            "CROSS JOIN nodes node ON node.node_name=replica.node_name AND node.last_heartbeat>? "
            "GROUP BY t.row,t.column HAVING COUNT(*)>=?", arguments))
    return found


def retry_reconstruction(connection: sqlite3.Connection, run: sqlite3.Row,
                         specification: dict[str, Any], now: float) -> dict[str, Any]:
    """Requeue a failed root's reconstruction without making new tile rows or artifacts."""

    if specification.get("program") != "dp_distributed" or run["parent_run_id"] is not None:
        raise ValueError("only a distributed DP root can retry reconstruction")
    if run["state"] != "failed":
        raise ValueError("only a failed reconstruction can be retried")
    total, complete = connection.execute(
        "SELECT COUNT(*),SUM(child.state='complete') FROM distributed_tiles t "
        "LEFT JOIN runs child ON child.run_id=t.child_run_id WHERE t.parent_run_id=?",
        (run["run_id"],)).fetchone()
    lease = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
    if not total or complete != total or len(durable_tiles(connection, run["run_id"], now, lease,
                                                         dependency_copies(connection))) != total:
        raise ValueError("reconstruction retry requires every original tile to be complete and durable")
    budget = dp_estimate(specification)["budget"]
    connection.execute(
        "UPDATE runs SET state='queued',control_state='running',node_name=NULL,lease_token=NULL,"
        "lease_expires=NULL,finished=NULL,error=NULL,failure_kind=NULL,stop_requested=0,"
        "engine_failures=0,progress_done=?,progress_total=?,progress_checkpoint_done=?,"
        "progress_phase='reconstructing',progress_message='retrying from preserved tiles',"
        "last_progress_at=? WHERE run_id=?",
        (budget * budget, budget * budget, budget * budget, now, run["run_id"]))
    return {"run_id": run["run_id"], "state": "queued", "retained_tiles": total}


# The same cover as tiles.dependencies(), as grid coordinates only: building full tile
# descriptors for every blocked tile dominated scheduling passes on large roots.
def predecessor_coordinates(p: int, side: int, row: int, column: int):
    """Yield (row, column) of every earlier tile meeting this tile's halo, in dependencies() order."""

    halo = p * p
    first_row = max(0, (row * side - halo) // side)
    first_column = max(0, (column * side - halo) // side)
    for predecessor_row in range(first_row, row + 1):
        for predecessor_column in range(first_column, column + 1):
            if (predecessor_row, predecessor_column) != (row, column):
                yield predecessor_row, predecessor_column


# Dispatch roots by creating only ready child leases; parent state contains no bulk arrays.
def advance(connection: sqlite3.Connection, now: float, max_roots: int | None = None,
            parent_run_id: str | None = None) -> None:
    """Advance waiting DAGs inside the caller's transaction and queue reconstruction when durable."""

    # Reclaiming dead tiles schedules nothing, so it continues while dispatch is stopped.
    retire_finished_tiles(connection, now)
    campaign = connection.execute("SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0]
    if campaign != "running":
        return
    parents = connection.execute(
        "SELECT * FROM runs WHERE state='waiting' AND (? IS NULL OR run_id=?) "
        "ORDER BY priority DESC,estimated_seconds,created",
        (parent_run_id, parent_run_id)).fetchall()
    eligible = []
    for parent in parents:
        specification = json.loads(parent["specification"])
        if specification.get("program") != "dp_distributed":
            continue
        arguments = specification["arguments"]
        p, r, side = arguments["p"], arguments["r"], int(arguments.get("tile_side", 4096))
        count = (dp_estimate(specification)["budget"] + side - 1) // side
        if count * count > FULL_SCAN_TILES:
            last = _last_scan.get(parent["run_id"])
            if last is not None and 0 <= now - last < min(MAX_SCAN_INTERVAL, count * count / FULL_SCAN_TILES):
                continue
        eligible.append(parent)
    if max_roots is not None:
        # Fairness across roots matters more than rescanning a high-priority
        # blocked grid every second. Reconstruction still wins at lease time.
        eligible.sort(key=lambda parent: (_last_scan.get(parent["run_id"], float("-inf")),
                                          -parent["priority"], parent["created"]))
        eligible = eligible[:max_roots]
    for parent in eligible:
        _last_scan[parent["run_id"]] = now
        specification = json.loads(parent["specification"])
        arguments = specification["arguments"]
        p, r, side = arguments["p"], arguments["r"], int(arguments.get("tile_side", 4096))
        run_id = parent["run_id"]
        count = (dp_estimate(specification)["budget"] + side - 1) // side
        reuse_tiles(connection, parent, now)
        lease = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
        copies = dependency_copies(connection)
        total = count * count
        budget = math.isqrt(parent["progress_total"])
        unassigned = connection.execute(
            "SELECT 1 FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NULL LIMIT 1",
            (run_id,)).fetchone() is not None

        # Grid-wide bookkeeping needs every tile of the root, so it runs on a slower cycle.
        # The refresh costs time in proportion to the grid, so small roots refresh every pass.
        interval = min(REFRESH_SECONDS if unassigned else END_GAME_REFRESH_SECONDS, total / REFRESH_TILES_PER_SECOND)
        last = _last_refresh.get(run_id)
        if last is None or not 0 <= now - last < interval:
            _last_refresh[run_id] = now
            recompute_lost_tiles(connection, run_id, now)
            durable = durable_tiles(connection, run_id, now, lease, copies)
            done = sum(min(side, budget - row * side) * min(side, budget - column * side)
                       for row, column in durable)
            message = f"{len(durable)}/{total} replicated tiles"
            if done != parent["progress_done"] or message != parent["progress_message"]:
                connection.execute("UPDATE runs SET progress_done=?,progress_checkpoint_done=?,"
                                   "last_progress_at=CASE WHEN progress_done<? THEN ? ELSE last_progress_at END,"
                                   "progress_message=? WHERE run_id=?",
                                   (done, done, done, now, message, run_id))
            connection.execute(
                "DELETE FROM distributed_tile_retries WHERE parent_run_id=? AND EXISTS "
                "(SELECT 1 FROM distributed_tiles t JOIN runs child ON child.run_id=t.child_run_id "
                "WHERE t.parent_run_id=distributed_tile_retries.parent_run_id "
                "AND t.row=distributed_tile_retries.row AND t.column=distributed_tile_retries.column "
                "AND child.state='complete')", (run_id,))
            if len(durable) == total:
                connection.execute("UPDATE runs SET state='queued',progress_phase='reconstructing' WHERE run_id=?", (run_id,))
                continue

        retries = {(retry["row"], retry["column"]): retry for retry in connection.execute(
            "SELECT row,column,failures,next_retry FROM distributed_tile_retries WHERE parent_run_id=?",
            (run_id,))}

        # Failed tiles are found through the (parent, state) index, not by reading the grid.
        exhausted = None
        for failed in connection.execute(
                "SELECT t.row,t.column,t.child_run_id,r.error FROM runs r "
                "JOIN distributed_tiles t ON t.child_run_id=r.run_id "
                "WHERE r.parent_run_id=? AND r.state='failed'", (run_id,)).fetchall():
            row, column = failed["row"], failed["column"]
            key = (run_id, row, column)
            prior = retries.get((row, column))
            # A tile whose input copy went offline did nothing wrong; retry it without
            # spending one of its attempts, once the input is back or recomputed.
            predecessors = list(predecessor_coordinates(p, side, row, column))
            input_missing = len(durable_among(connection, run_id, predecessors, now, lease, copies)) < len(predecessors)
            failures = (prior["failures"] if prior else 0) + (0 if input_missing else 1)
            if failures > TILE_RETRY_LIMIT:
                exhausted = failed
                break
            delay = min(TILE_RETRY_MAX_SECONDS, TILE_RETRY_BASE_SECONDS * 2 ** max(0, failures - 1))
            connection.execute(
                "INSERT INTO distributed_tile_retries(parent_run_id,row,column,failures,next_retry) "
                "VALUES(?,?,?,?,?) ON CONFLICT(parent_run_id,row,column) DO UPDATE SET "
                "failures=excluded.failures,next_retry=excluded.next_retry",
                (*key, failures, now + delay))
            retries[(row, column)] = {"failures": failures, "next_retry": now + delay}
            connection.execute(
                "UPDATE distributed_tiles SET child_run_id=NULL WHERE parent_run_id=? AND row=? AND column=? "
                "AND child_run_id=?", (*key, failed["child_run_id"]))
        if exhausted is not None:
            connection.execute("UPDATE runs SET state='failed',finished=?,error=?,progress_phase='failed' WHERE run_id=?",
                               (now, f"tile {exhausted['row']},{exhausted['column']} failed repeatedly: "
                                f"{exhausted['error']}", run_id))
            continue

        # A tile is ready only when its predecessors are durable, and every tile has a
        # predecessor in the wave just before it, so no tile beyond one wave past the
        # newest assigned tile can be ready: examine only that frontier.
        newest = connection.execute(
            "SELECT MAX(row+column) FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NOT NULL",
            (run_id,)).fetchone()[0]
        frontier = [(row[0], row[1]) for row in connection.execute(
            "SELECT row,column FROM distributed_tiles WHERE parent_run_id=? AND child_run_id IS NULL "
            "AND row+column<=?", (run_id, (-1 if newest is None else newest) + 1))]
        frontier = [key for key in frontier
                    if key not in retries or now >= retries[key]["next_retry"]]
        if not frontier:
            continue
        needed = {key: list(predecessor_coordinates(p, side, *key)) for key in frontier}
        durable = durable_among(connection, run_id, [pred for preds in needed.values() for pred in preds],
                                now, lease, copies)
        for key in sorted(frontier):
            if any(pred not in durable for pred in needed[key]):
                continue
            child_specification_value = child_specification(parent, *key)
            child = str(uuid.uuid4())
            connection.execute("INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,estimated_seconds,parent_run_id) VALUES(?,?,?,'queued',?,0,?,?,?)",
                               (child,calculation_id(child_specification_value),canonical_json(child_specification_value).decode(),parent["priority"],now,parent["estimated_seconds"],run_id))
            connection.execute("UPDATE distributed_tiles SET child_run_id=? WHERE parent_run_id=? AND row=? AND column=?",
                               (child,run_id,*key))


AFFINITY_MAX_WAIT_SECONDS = 900


# Affinity: a tile's successors read its edge bands, which live only on the node that
# produced it. Inputs already on the node fetched in a median 0.8 s against 3.2 s
# (p90 2.0 s against 15.7 s) for tiles with any remote input.
def locality_scores(connection: sqlite3.Connection, node_name: str, items: list, now: float) -> dict[str, int]:
    """Score queued tiles for node_name; higher means more predecessor data is already on its disk.

    A neighbour this node produced (its bands are here) adds 3 when it is the left one,
    2 the upper, 1 the diagonal, keeping a node moving along its row; one whose packet
    is merely stored here adds 2 (left) or 1 (upper). A tile that has waited
    longer than AFFINITY_MAX_WAIT_SECONDS scores above all of them, so preference can
    delay a tile by at most that long. The scores are only a tie-break among tiles of
    one root and priority; they never change which roots or phases are served first.
    """

    wanted = {}
    for run_id, specification, created in items:
        if specification.get("program") != "dp_tile":
            continue
        arguments = specification["arguments"]
        if now - created > float(os.environ.get("KH_ROW_AFFINITY_WAIT", AFFINITY_MAX_WAIT_SECONDS)):
            wanted[run_id] = None
            continue
        wanted[run_id] = (arguments["parent_run_id"], arguments["row"], arguments["column"])
    scores = {run_id: 7 for run_id, key in wanted.items() if key is None}
    # (up, left) offset -> (score if produced here, score if only the packet is stored here)
    weights = {(0, 1): (3, 2), (1, 0): (2, 1), (1, 1): (1, 0)}
    offsets = tuple(weights)
    neighbours = []
    for key in wanted.values():
        if key is None:
            continue
        parent, row, column = key
        neighbours.extend((parent, row - up, column - left) for up, left in offsets
                          if row - up >= 0 and column - left >= 0)
    held = {}
    neighbours = list(dict.fromkeys(neighbours))
    for start in range(0, len(neighbours), 300):
        chunk = neighbours[start:start + 300]
        values = ",".join("(?,?,?)" for _ in chunk)
        for parent, row, column, producer, stored in connection.execute(
                f"WITH want(parent,row,column) AS (VALUES {values}) "
                "SELECT w.parent,w.row,w.column,r.node_name,"
                "EXISTS(SELECT 1 FROM replicas x WHERE x.artifact_hash=r.artifact_hash AND x.node_name=?) "
                "FROM want w JOIN distributed_tiles t ON t.parent_run_id=w.parent AND t.row=w.row AND t.column=w.column "
                "JOIN runs r ON r.run_id=t.child_run_id AND r.state='complete'",
                [value for key in chunk for value in key] + [node_name]):
            held[(parent, row, column)] = (producer == node_name, bool(stored))
    for run_id, key in wanted.items():
        if key is None:
            continue
        parent, row, column = key
        score = 0
        for (up, left), (if_produced, if_stored) in weights.items():
            produced, stored = held.get((parent, row - up, column - left), (False, False))
            score += if_produced if produced else if_stored if stored else 0
        if score:
            scores[run_id] = score
    return scores


# Page only the immutable predecessor descriptions required by the currently owned task.
def cpu_fallback_allowed(connection: sqlite3.Connection) -> bool:
    """Whether a tile that finds its host's GPU busy may compute on its CPUs instead (setting dp_cpu_fallback).

    Off unless an operator turns it on (the dashboard's Activity tab). A heavy tile that gave up on
    the GPU after 11-14 minutes then took 25-50 minutes on its CPUs, often on the field's critical
    path, while the GPU would have served it within minutes. A tile with no usable GPU (too big for
    it, a broken GPU, or one held for a long matching) still uses its CPUs either way.
    """

    row = connection.execute("SELECT value FROM settings WHERE key='dp_cpu_fallback'").fetchone()
    return row is not None and row[0] == "1"


def inputs(connection: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any], now: float) -> dict[str, Any]:
    """Return a bounded page of live artifact sources, enforcing task-specific tile scope."""

    specification = json.loads(run["specification"])
    arguments = specification["arguments"]
    program = specification["program"]
    if "publish_bands" in request:
        return publish_bands(connection, run, request, now)
    if program == "dp_tile":
        parent = arguments["parent_run_id"]
        target = tile(arguments["p"],arguments["r"],arguments["tile_side"],arguments["row"],arguments["column"])
        wanted = dependencies(arguments["p"],arguments["r"],arguments["tile_side"],target)
    elif program == "dp_distributed":
        parent = run["run_id"]
        wanted = [tile(arguments["p"],arguments["r"],int(arguments.get("tile_side",4096)),int(request["row"]),int(request["column"]))]
    else:
        raise ValueError("run is not a distributed tile task")
    offset = request.get("offset",0)
    if type(offset) is not int or offset < 0 or offset > len(wanted):
        raise ValueError("invalid tile input page")
    records = []
    for target in wanted[offset:offset+64]:
        row = connection.execute("SELECT r.* FROM distributed_tiles t JOIN runs r ON r.run_id=t.child_run_id WHERE t.parent_run_id=? AND t.row=? AND t.column=?",
                                 (parent,target.row,target.column)).fetchone()
        descriptor = None if row is None else artifact_record(connection,row,now,run["node_name"])
        if not descriptor or not descriptor["locations"]:
            raise ValueError("tile dependency has no live complete artifact")
        record = {"row":target.row,"column":target.column,**descriptor}
        if program == "dp_tile":
            # The whole packet stays in the record: it is the fallback for any band problem.
            band = band_record(connection, descriptor["sha256"], band_kind(tile(arguments["p"],arguments["r"],arguments["tile_side"],arguments["row"],arguments["column"]), target), now, run["node_name"])
            if band is not None:
                record["band"] = band
        records.append(record)
    next_offset = offset+len(records)
    return {"records":records,"next":next_offset if next_offset<len(wanted) else None,
            "cpu_fallback":cpu_fallback_allowed(connection)}
