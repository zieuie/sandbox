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

REUSE_BATCH = 128
MAX_BAND_BYTES = 2 * 1024**3
TILE_RETRY_LIMIT = 3
TILE_RETRY_BASE_SECONDS = 30
TILE_RETRY_MAX_SECONDS = 300


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
    if "max_cpus" in arguments:
        result["arguments"]["max_cpus"] = arguments["max_cpus"]
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
RETIRE_BATCH = 500
_ACTIVE_ROOT = "('waiting','queued','running','stopping','paused')"


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
        "WHERE x.artifact_hash=p.artifact_hash AND n.last_heartbeat>?)>=2) "
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


# Dispatch roots by creating only ready child leases; parent state contains no bulk arrays.
def advance(connection: sqlite3.Connection, now: float) -> None:
    """Advance waiting DAGs inside the caller's transaction and queue reconstruction when durable."""

    # Reclaiming dead tiles schedules nothing, so it continues while dispatch is stopped.
    retire_finished_tiles(connection, now)
    campaign = connection.execute("SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0]
    if campaign != "running":
        return
    parents = connection.execute("SELECT * FROM runs WHERE state='waiting' ORDER BY priority DESC,estimated_seconds,created").fetchall()
    for parent in parents:
        specification = json.loads(parent["specification"])
        if specification.get("program") != "dp_distributed":
            continue
        arguments = specification["arguments"]
        p, r, side = arguments["p"], arguments["r"], int(arguments.get("tile_side", 4096))
        reuse_tiles(connection, parent, now)
        rows = connection.execute(
            "SELECT t.row,t.column,t.child_run_id,r.* FROM distributed_tiles t LEFT JOIN runs r ON r.run_id=t.child_run_id "
            "WHERE t.parent_run_id=? ORDER BY t.row,t.column", (parent["run_id"],),
        ).fetchall()
        lease = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
        durable = {(row[0], row[1]) for row in connection.execute(
            "SELECT t.row,t.column FROM distributed_tiles t "
            "JOIN runs child ON child.run_id=t.child_run_id "
            "JOIN replicas replica ON replica.artifact_hash=child.artifact_hash "
            "JOIN nodes node ON node.node_name=replica.node_name "
            "WHERE t.parent_run_id=? AND child.state='complete' "
            "AND node.last_heartbeat>? GROUP BY t.row,t.column HAVING COUNT(*)>=2",
            (parent["run_id"], now - lease))}
        budget = math.isqrt(parent["progress_total"])
        done = sum(min(side, budget - row * side) * min(side, budget - column * side)
                   for row, column in durable)
        message = f"{len(durable)}/{len(rows)} replicated tiles"
        if done != parent["progress_done"] or message != parent["progress_message"]:
            connection.execute("UPDATE runs SET progress_done=?,progress_checkpoint_done=?,"
                               "last_progress_at=CASE WHEN progress_done<? THEN ? ELSE last_progress_at END,"
                               "progress_message=? WHERE run_id=?",
                               (done, done, done, now, message, parent["run_id"]))
        retries = {(retry["row"], retry["column"]): retry for retry in connection.execute(
            "SELECT row,column,failures,next_retry FROM distributed_tile_retries WHERE parent_run_id=?",
            (parent["run_id"],))}
        exhausted = None
        for row in rows:
            if row["state"] != "failed":
                continue
            key = (parent["run_id"], row["row"], row["column"])
            prior = retries.get((row["row"], row["column"]))
            failures = (prior["failures"] if prior else 0) + 1
            if failures > TILE_RETRY_LIMIT:
                exhausted = row
                break
            delay = min(TILE_RETRY_MAX_SECONDS, TILE_RETRY_BASE_SECONDS * 2 ** (failures - 1))
            connection.execute(
                "INSERT INTO distributed_tile_retries(parent_run_id,row,column,failures,next_retry) "
                "VALUES(?,?,?,?,?) ON CONFLICT(parent_run_id,row,column) DO UPDATE SET "
                "failures=excluded.failures,next_retry=excluded.next_retry",
                (*key, failures, now + delay))
            retries[(row["row"], row["column"])] = {"failures": failures, "next_retry": now + delay}
            connection.execute(
                "UPDATE distributed_tiles SET child_run_id=NULL WHERE parent_run_id=? AND row=? AND column=? "
                "AND child_run_id=?", (*key, row["child_run_id"]))
        if exhausted is not None:
            connection.execute("UPDATE runs SET state='failed',finished=?,error=?,progress_phase='failed' WHERE run_id=?",
                               (now, f"tile {exhausted['row']},{exhausted['column']} failed repeatedly: "
                                f"{exhausted['error']}", parent["run_id"]))
            continue
        connection.execute(
            "DELETE FROM distributed_tile_retries WHERE parent_run_id=? AND EXISTS "
            "(SELECT 1 FROM distributed_tiles t JOIN runs child ON child.run_id=t.child_run_id "
            "WHERE t.parent_run_id=distributed_tile_retries.parent_run_id "
            "AND t.row=distributed_tile_retries.row AND t.column=distributed_tile_retries.column "
            "AND child.state='complete')", (parent["run_id"],))
        if len(durable) == len(rows):
            connection.execute("UPDATE runs SET state='queued',progress_phase='reconstructing' WHERE run_id=?", (parent["run_id"],))
            continue
        halo = p * p
        for row in rows:
            if row["child_run_id"]:
                continue
            retry = retries.get((row["row"], row["column"]))
            if retry is not None and now < retry["next_retry"]:
                continue
            target_row, target_column = row["row"], row["column"]
            # dependencies() constructs full tile descriptors; only their grid
            # coordinates matter until this child is actually ready to queue.
            first_row = max(0, (target_row * side - halo) // side)
            first_column = max(0, (target_column * side - halo) // side)
            if any((predecessor_row, predecessor_column) not in durable
                   for predecessor_row in range(first_row, target_row + 1)
                   for predecessor_column in range(first_column, target_column + 1)
                   if (predecessor_row, predecessor_column) != (target_row, target_column)):
                continue
            target = tile(p,r,side,target_row,target_column)
            child_specification_value = child_specification(parent, target.row, target.column)
            child = str(uuid.uuid4())
            connection.execute("INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,estimated_seconds,parent_run_id) VALUES(?,?,?,'queued',?,0,?,?,?)",
                               (child,calculation_id(child_specification_value),canonical_json(child_specification_value).decode(),parent["priority"],now,parent["estimated_seconds"],parent["run_id"]))
            connection.execute("UPDATE distributed_tiles SET child_run_id=? WHERE parent_run_id=? AND row=? AND column=?",
                               (child,parent["run_id"],target.row,target.column))


AFFINITY_MAX_WAIT_SECONDS = 900


# Row affinity: the node that finished a tile's left neighbour is the natural one to run it next.
def locality_scores(connection: sqlite3.Connection, node_name: str, items: list, now: float) -> dict[str, int]:
    """Score queued tiles for node_name; higher means more predecessor data is already on its disk.

    Left neighbour produced here scores 3, merely stored here 2; an upper
    neighbour stored here adds 1. A tile that has waited longer than
    AFFINITY_MAX_WAIT_SECONDS scores above all of them, so preference can delay a
    tile by at most that long. The scores are only a tie-break among tiles of one
    root and priority; they never change which roots or phases are served first.
    """

    wanted = {}
    for run_id, specification, created in items:
        if specification.get("program") != "dp_tile":
            continue
        arguments = specification["arguments"]
        if now - created > float(os.environ.get("KH_ROW_AFFINITY_WAIT", AFFINITY_MAX_WAIT_SECONDS)):
            wanted[run_id] = None
            continue
        row, column = arguments["row"], arguments["column"]
        wanted[run_id] = (arguments["parent_run_id"], row, column)
    scores = {run_id: 5 for run_id, key in wanted.items() if key is None}
    neighbours = []
    for run_id, key in wanted.items():
        if key is None:
            continue
        parent, row, column = key
        if column > 0:
            neighbours.append((parent, row, column - 1))
        if row > 0:
            neighbours.append((parent, row - 1, column))
    held = {}
    for start in range(0, len(neighbours), 300):
        chunk = list(dict.fromkeys(neighbours[start:start + 300]))
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
        produced, stored = held.get((parent, row, column - 1), (False, False)) if column > 0 else (False, False)
        score += 3 if produced else 2 if stored else 0
        _, up = held.get((parent, row - 1, column), (False, False)) if row > 0 else (False, False)
        score += 1 if up else 0
        if score:
            scores[run_id] = score
    return scores


# Page only the immutable predecessor descriptions required by the currently owned task.
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
    return {"records":records,"next":next_offset if next_offset<len(wanted) else None}
