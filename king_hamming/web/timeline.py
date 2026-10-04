"""Per-machine lease timeline: what ran where, and the gaps between."""

from __future__ import annotations

import sqlite3
from typing import Callable

TIMELINE_SECONDS = 24 * 3600
MERGE_GAP = 20.0  # seconds; back-to-back leases of one field read as one bar
OUTCOMES = {"complete": "ok", "running": "running", "engine retry": "retry",
            "fail": "fail", "lease expired": "expired", "intentional_stop": "stop"}


def kind_of(description: dict) -> str:
    return {"dp_tile": "tile", "matching": "matching", "dp_distributed": "root",
            "dp": "whole"}.get(description["kind"], "other")


def build_timeline(connection: sqlite3.Connection, node_names: list[str],
                   describe: Callable[[dict], dict], now: float,
                   window: float = TIMELINE_SECONDS, merge_gap: float = MERGE_GAP) -> dict:
    """Lease segments over `window`. Back-to-back completed leases of one field on
    one machine merge when the gap between them is at most `merge_gap` seconds."""
    start = now - window
    leases = [dict(row) for row in connection.execute(
        "SELECT h.lease_token,h.run_id,h.node_name,h.started,h.finished,h.outcome,"
        "r.specification,r.calculation_id FROM lease_history h JOIN runs r USING(run_id) "
        "WHERE COALESCE(h.finished,?)>? ORDER BY h.started", (now, start))]
    by_token = {lease["lease_token"]: lease for lease in leases}
    # Matching shards run on partner machines under the coordinator's lease.
    partners = connection.execute(
        "SELECT DISTINCT lease_token,node_name FROM resource_usage_samples "
        "WHERE component='shard' AND recorded>?", (start,)).fetchall()

    raw: list[dict] = []
    for lease, role, node in ([(lease, "lease", lease["node_name"]) for lease in leases] +
                              [(by_token[token], "partner", name) for token, name in partners
                               if token in by_token and by_token[token]["node_name"] != name]):
        description = describe(lease)
        field = description.get("field")
        raw.append({
            "n": node, "t0": max(lease["started"], start), "t1": lease["finished"],
            "f": f"{field[0]},{field[1]}" if field else None,
            "k": kind_of(description) + ("_partner" if role == "partner" else ""),
            "o": OUTCOMES.get(lease["outcome"], "other") if role == "lease" or lease["finished"] else "running",
            "c": 1, "a": description.get("label"), "b": description.get("label"),
        })

    segments: list[dict] = []
    open_by_key: dict[tuple, dict] = {}
    for item in sorted(raw, key=lambda entry: (entry["n"], entry["t0"])):
        key = (item["n"], item["f"], item["k"])
        previous = open_by_key.get(key)
        if (previous is not None and item["o"] == "ok" and previous["o"] == "ok" and
                previous["t1"] is not None and item["t0"] - previous["t1"] <= merge_gap):
            previous["t1"] = item["t1"]
            previous["c"] += 1
            previous["b"] = item["b"]
            continue
        segments.append(item)
        open_by_key[key] = item

    lanes: dict[str, int] = {}
    ends: dict[str, list[float]] = {}
    for item in sorted(segments, key=lambda entry: (entry["n"], entry["t0"])):
        row = ends.setdefault(item["n"], [])
        end = item["t1"] if item["t1"] is not None else now
        for index, finish in enumerate(row):
            if finish <= item["t0"] + 1e-6:
                row[index] = end
                item["lane"] = index
                break
        else:
            item["lane"] = len(row)
            row.append(end)
        lanes[item["n"]] = max(lanes.get(item["n"], 1), item["lane"] + 1)

    for item in segments:  # whole seconds are plenty on a 24-hour axis, and a third smaller to send
        item["t0"] = int(item["t0"])
        if item["t1"] is not None:
            item["t1"] = int(item["t1"] + 0.999)
    return {"start": start, "end": now,
            "nodes": [{"name": name, "lanes": lanes.get(name, 1)} for name in node_names],
            "segments": segments}
