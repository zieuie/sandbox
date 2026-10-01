"""Durable DP tile DAG scheduling and bounded dependency descriptions for the leader."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import json
import sqlite3
import uuid
from typing import Any

from common import calculation_id, canonical_json
from dp_solver.scheduling import dp_estimate
from dp_solver.tiles import dependencies, memory_bytes, tile

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
"""

REUSE_BATCH = 128
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
def artifact_record(connection: sqlite3.Connection, row: sqlite3.Row, now: float) -> dict[str, Any] | None:
    """Return row's artifact descriptor with live sources, or None if it is not complete."""

    if row["state"] != "complete" or not row["artifact_hash"]:
        return None
    deadline = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
    sources = [record[0] for record in connection.execute(
        "SELECT r.location FROM replicas r JOIN nodes n USING(node_name) WHERE r.artifact_hash=? AND n.last_heartbeat>? ORDER BY node_name",
        (row["artifact_hash"], now-deadline),
    )]
    size = connection.execute("SELECT size FROM artifacts WHERE artifact_hash=?", (row["artifact_hash"],)).fetchone()[0]
    return {"sha256": row["artifact_hash"], "size": size, "locations": sources}


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


# Dispatch roots by creating only ready child leases; parent state contains no bulk arrays.
def advance(connection: sqlite3.Connection, now: float) -> None:
    """Advance waiting DAGs inside the caller's transaction and queue reconstruction when durable."""

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
        lookup = {(row["row"], row["column"]): row for row in rows}
        available = {key: artifact_record(connection, row, now) for key, row in lookup.items() if row["child_run_id"]}
        durable = {key for key, record in available.items() if record and len(record["locations"]) >= 2}
        done = sum((tile(p,r,side,*key).value_bytes//8) for key in durable)
        connection.execute("UPDATE runs SET progress_done=?,progress_checkpoint_done=?,last_progress_at=CASE WHEN progress_done<? THEN ? ELSE last_progress_at END,progress_message=? WHERE run_id=?",
                           (done, done, done, now, f"{len(durable)}/{len(rows)} replicated tiles", parent["run_id"]))
        exhausted = None
        for row in rows:
            if row["state"] != "failed":
                continue
            key = (parent["run_id"], row["row"], row["column"])
            prior = connection.execute(
                "SELECT failures FROM distributed_tile_retries WHERE parent_run_id=? AND row=? AND column=?",
                key).fetchone()
            failures = (prior[0] if prior else 0) + 1
            if failures > TILE_RETRY_LIMIT:
                exhausted = row
                break
            delay = min(TILE_RETRY_MAX_SECONDS, TILE_RETRY_BASE_SECONDS * 2 ** (failures - 1))
            connection.execute(
                "INSERT INTO distributed_tile_retries(parent_run_id,row,column,failures,next_retry) "
                "VALUES(?,?,?,?,?) ON CONFLICT(parent_run_id,row,column) DO UPDATE SET "
                "failures=excluded.failures,next_retry=excluded.next_retry",
                (*key, failures, now + delay))
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
        for row in rows:
            if row["child_run_id"]:
                continue
            retry = connection.execute(
                "SELECT next_retry FROM distributed_tile_retries WHERE parent_run_id=? AND row=? AND column=?",
                (parent["run_id"], row["row"], row["column"])).fetchone()
            if retry is not None and now < retry[0]:
                continue
            target = tile(p,r,side,row["row"],row["column"])
            if any((item.row,item.column) not in durable for item in dependencies(p,r,side,target)):
                continue
            child_specification_value = child_specification(parent, target.row, target.column)
            child = str(uuid.uuid4())
            connection.execute("INSERT INTO runs(run_id,calculation_id,specification,state,priority,from_scratch,created,estimated_seconds,parent_run_id) VALUES(?,?,?,'queued',?,0,?,?,?)",
                               (child,calculation_id(child_specification_value),canonical_json(child_specification_value).decode(),parent["priority"],now,parent["estimated_seconds"],parent["run_id"]))
            connection.execute("UPDATE distributed_tiles SET child_run_id=? WHERE parent_run_id=? AND row=? AND column=?",
                               (child,parent["run_id"],target.row,target.column))


# Page only the immutable predecessor descriptions required by the currently owned task.
def inputs(connection: sqlite3.Connection, run: sqlite3.Row, request: dict[str, Any], now: float) -> dict[str, Any]:
    """Return a bounded page of live artifact sources, enforcing task-specific tile scope."""

    specification = json.loads(run["specification"])
    arguments = specification["arguments"]
    program = specification["program"]
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
        descriptor = None if row is None else artifact_record(connection,row,now)
        if not descriptor or not descriptor["locations"]:
            raise ValueError("tile dependency has no live complete artifact")
        records.append({"row":target.row,"column":target.column,**descriptor})
    next_offset = offset+len(records)
    return {"records":records,"next":next_offset if next_offset<len(wanted) else None}
