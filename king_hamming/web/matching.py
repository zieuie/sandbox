"""Matching view: live and past bipartite matching runs, phase by phase.

The native engine commits a checkpoint at every phase boundary; the leader's
`checkpoints` table keeps each one's phase number (cursor) and matched-request
count (done), so a run's convergence can be drawn during and after the run.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

MATCH_PROGRAMS = ("match", "match_distributed", "match_partitioned", "match_gpu", "match_gpu_blocks")


def build_matching(connection: sqlite3.Connection, names: dict[str, str],
                   describe: Callable[[dict], dict], health: Callable[[dict, float], str],
                   now: float) -> dict:
    runs = [dict(row) for row in connection.execute(
        "SELECT * FROM runs WHERE parent_run_id IS NULL AND (" +
        " OR ".join("specification LIKE ?" for _ in MATCH_PROGRAMS) + ") ORDER BY created",
        [f'%"program":"{program}"%' for program in MATCH_PROGRAMS])]
    if not runs:
        return {"runs": []}
    identifiers = [run["run_id"] for run in runs]
    marks = ",".join("?" for _ in identifiers)
    phases: dict[str, list] = {identifier: [] for identifier in identifiers}
    for row in connection.execute(
            f"SELECT run_id,cursor,done,created FROM checkpoints WHERE run_id IN ({marks}) "
            "ORDER BY run_id,cursor,created", identifiers):
        series = phases[row["run_id"]]
        if series and series[-1][0] == row["cursor"]:
            series[-1] = [row["cursor"], row["done"], row["created"]]  # keep the newest copy
        else:
            series.append([row["cursor"], row["done"], row["created"]])
    usage: dict[str, list] = {identifier: [] for identifier in identifiers}
    for row in connection.execute(
            f"SELECT run_id,node_name,component,shard_index,cpu_microseconds,peak_rss_bytes,recorded "
            f"FROM resource_usage WHERE run_id IN ({marks}) ORDER BY run_id,shard_index", identifiers):
        usage[row["run_id"]].append({
            "machine": names.get(row["node_name"], row["node_name"]), "component": row["component"],
            "shard": row["shard_index"], "cpu_seconds": round(row["cpu_microseconds"] / 1e6, 1),
            "peak_rss": row["peak_rss_bytes"]})
    reserved: dict[str, list[str]] = {}
    for row in connection.execute(
            f"SELECT run_id,node_name FROM node_reservations WHERE run_id IN ({marks}) ORDER BY node_name",
            identifiers):
        reserved.setdefault(row["run_id"], []).append(names.get(row["node_name"], row["node_name"]))

    result = []
    for run in runs:
        description = describe(run)
        arguments = json.loads(run["specification"]).get("arguments", {})
        done, total = run["progress_done"], run["progress_total"]
        outcome = None
        if run["state"] == "complete" and total:
            outcome = "matched" if done >= total else "obstructed"
        coordinator = names.get(run["node_name"], run["node_name"]) if run["node_name"] else None
        machines = [coordinator] if coordinator else []
        for machine in reserved.get(run["run_id"], []) + [item["machine"] for item in usage[run["run_id"]]]:
            if machine and machine not in machines:
                machines.append(machine)
        gpu = None
        phase_rows = None
        if description.get("program") in {"match_gpu", "match_gpu_blocks"}:
            gpu = {"index": run.get("gpu_index"), "device": None, "phases": None, "seconds": None}
            try:
                summary = json.loads(run["progress_message"] or "{}")
            except ValueError:
                summary = {}
            if isinstance(summary, dict):
                trace = summary.get("trace")
                if isinstance(trace, list) and run["state"] == "complete":
                    # The chart reads checkpoint-like rows: [step, matched, time]. A GPU run has no
                    # checkpoints, so rebuild them from the kernel's own record of unmatched counts.
                    when = run["finished"] or run["started"] or 0
                    phase_rows = [[item[0], (total or 0) - item[1], when] for item in trace[1:]
                                  if isinstance(item, list) and len(item) == 2 and
                                  all(type(value) is int for value in item)]
                gpu.update(device=summary.get("device"), phases=summary.get("phases"),
                           seconds=summary.get("seconds"), scans=summary.get("scans"),
                           blocks=summary.get("blocks"), rounds=summary.get("rounds"),
                           residual_round1=summary.get("residual_round1"))
        result.append({
            "run_id": run["run_id"], "field": description.get("field"), "program": description.get("program"),
            "engine": "gpu" if gpu else "cpu", "gpu": gpu,
            "poly": arguments.get("poly"), "workers": arguments.get("workers", 1),
            "threads": arguments.get("threads"), "state": run["state"], "health": health(run, now),
            "created": run["created"], "started": run["started"], "finished": run["finished"],
            "done": done, "total": total or None, "phase_label": run["progress_phase"],
            "last_progress_at": run["last_progress_at"], "attempt": run.get("lease_attempt"),
            "error": (run["error"] or "")[:300] or None, "outcome": outcome,
            "machines": machines, "phases": phase_rows if phase_rows is not None else phases[run["run_id"]],
            "step_label": ("block, then round" if description.get("program") == "match_gpu_blocks"
                           else "greedy, then phase" if gpu else "phase"), "resources": usage[run["run_id"]],
        })
    rank = {"running": 0, "stopping": 0, "queued": 1, "waiting": 1, "paused": 2}
    result.sort(key=lambda item: (rank.get(item["state"], 3), -(item["finished"] or item["created"] or 0)))
    return {"runs": result}
