"""Earliest-completion placement of DP tiles (docs/GPU_TILE_PLACEMENT_PLAN.md, sections 1-3 and 5).

Off unless the leader setting `dp_tile_placement` is "ect" ("pull", the default, is the queue order
the leader has always used). With "ect", when a machine asks for work:

- **Order:** a field's tiles go lowest anti-diagonal first (the critical path), not cheapest-first.
  Reconstruction first, priority and fairness between fields are unchanged.
- **Leave it for a faster GPU:** a *critical* tile (on one of its field's lowest unfinished
  anti-diagonals, or any tile once the field is in its narrow tail) is skipped for this machine
  when another live GPU would finish it sooner by more than one kernel on the fastest GPU. That
  machine asks within seconds; this one takes a non-critical tile instead. Nothing is pushed, so
  a machine that dies simply stops asking; leases and recovery are unchanged.

The estimates come from completed tiles: each machine's median kernel time on that field, and its
median overhead before (fetch, halo) and after (pack, publish, round trips) the kernel. They are
cached and refreshed every REFRESH_SECONDS, so the lease path stays a few dictionary lookups.

Simulated on 31^7's last 18 hours (dp_solver/placement_sim.py): 19.2 h becomes 17.1 h, and the
tail (last ~40 diagonals) 97 min becomes 40 min, mostly because merlin's GPU goes from 78% to 99%
busy instead of the P600s holding tiles it would finish 12 times faster.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from typing import Any

SETTING = "dp_tile_placement"
CRITICAL_DIAGONALS = 3       # the lowest unfinished anti-diagonals of a field count as critical
TAIL_TILES = 820             # about the last 40 anti-diagonals: every tile counts as critical
REFRESH_SECONDS = 30.0
RUNNING_SECONDS = 2.0        # how long a snapshot of running tiles is reused
HISTORY = 200                # completed tiles per machine consulted for its estimates
BEFORE_KERNEL = ("starting", "fetching", "waiting for GPU")


def enabled(connection) -> bool:
    row = connection.execute("SELECT value FROM settings WHERE key=?", (SETTING,)).fetchone()
    return row is not None and str(row[0]).strip().lower() == "ect"


def tile_of(specification: dict) -> tuple[str, int, int] | None:
    """(root, row, column) of a DP tile job, else None."""
    if specification.get("program") != "dp_tile":
        return None
    arguments = specification.get("arguments", {})
    try:
        return str(arguments["parent_run_id"]), int(arguments["row"]), int(arguments["column"])
    except (KeyError, TypeError, ValueError):
        return None


def order(candidates: list[dict]) -> list[dict]:
    """The leader's candidate order with a field's tiles sorted by anti-diagonal, not estimate.

    The leading keys are the leader's own (reconstruction first, priority, running tiles of the
    same root), so this only reorders tiles that were already tied on those.
    """
    def key(candidate: dict):
        position = tile_of(json.loads(candidate["specification"]))
        diagonal = position[1] + position[2] if position else -1
        estimate = -1.0 if candidate["estimated_seconds"] is None else candidate["estimated_seconds"]
        return (candidate["progress_phase"] != "reconstructing", -candidate["priority"], candidate["peers"],
                diagonal, estimate if position is None else 0.0, candidate["created"], candidate["run_id"])
    return sorted(candidates, key=key)


class Model:
    """Cached per-field progress and per-machine service times (one per leader process)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.fields: dict[str, dict] = {}          # root -> {"lowest", "unfinished", "at"}
        self.kernels: dict[tuple[str, str], float] = {}  # (node, root) -> median kernel seconds
        self.overheads: dict[str, tuple[float, float]] = {}  # node -> (before, after) seconds
        self.kernels_at: dict[str, float] = {}
        self.gpu_names: dict[str, str] = {}             # node -> its GPU model (for estimates without history)
        self.running: tuple[float, dict[str, dict[str, int]]] = (0.0, {})

    def field(self, connection, root: str, now: float) -> dict:
        with self.lock:
            cached = self.fields.get(root)
            if cached and now - cached["at"] < REFRESH_SECONDS:
                return cached
        lowest, unfinished = connection.execute(
            "SELECT MIN(t.row+t.column),COUNT(*) FROM distributed_tiles t LEFT JOIN runs c ON c.run_id=t.child_run_id "
            "WHERE t.parent_run_id=? AND (c.state IS NULL OR c.state<>'complete')", (root,)).fetchone()
        ready = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE parent_run_id=? AND state='queued'", (root,)).fetchone()[0]
        value = {"lowest": lowest, "unfinished": unfinished or 0, "ready": ready or 0, "at": now}
        with self.lock:
            self.fields[root] = value
        return value

    def service(self, connection, root: str, now: float) -> None:
        """Refresh every machine's kernel median on this field and its overheads."""
        with self.lock:
            if now - self.kernels_at.get(root, 0.0) < REFRESH_SECONDS:
                return
            self.kernels_at[root] = now
        kernels: dict[str, list[float]] = {}
        before: dict[str, list[float]] = {}
        after: dict[str, list[float]] = {}
        for node, started, finished, details in connection.execute(
                "SELECT node_name,started,finished,progress_details FROM runs WHERE parent_run_id=? "
                "AND state='complete' AND finished>? ORDER BY finished DESC LIMIT ?",
                (root, now - 6 * 3600, HISTORY * 12)):
            try:
                values = json.loads(details or "{}")
                kernel = float(values["kernel_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if values.get("engine") != "gpu" or len(kernels.setdefault(node, [])) >= HISTORY:
                continue
            kernels[node].append(kernel)
            pre = float(values.get("fetch_seconds", 0)) + float(values.get("halo_seconds", 0))
            busy = pre + float(values.get("gpu_wait_seconds", 0)) + kernel
            before.setdefault(node, []).append(pre)
            after.setdefault(node, []).append(max(0.0, (finished or 0) - (started or 0) - busy))
        names = {row[0]: gpu_name({"gpus_json": row[1]}) for row in connection.execute(
            "SELECT node_name,gpus_json FROM nodes")}
        with self.lock:
            self.gpu_names.update({name: value for name, value in names.items() if value})
            for node, values in kernels.items():
                if len(values) >= 5:
                    self.kernels[(node, root)] = statistics.median(values)
                    self.overheads[node] = (statistics.median(before[node]), statistics.median(after[node]))

    def running_tiles(self, connection, now: float) -> dict[str, dict[str, int]]:
        """Per machine: running DP tiles before their kernel, in it, and in total."""
        with self.lock:
            at, snapshot = self.running
            if now - at < RUNNING_SECONDS:
                return snapshot
        snapshot: dict[str, dict[str, int]] = {}
        for node, phase, count in connection.execute(
                "SELECT node_name,progress_phase,COUNT(*) FROM runs WHERE state='running' "
                "AND json_extract(specification,'$.program')='dp_tile' GROUP BY node_name,progress_phase"):
            entry = snapshot.setdefault(node, {"before": 0, "computing": 0, "total": 0})
            entry["total"] += count
            if phase in BEFORE_KERNEL:
                entry["before"] += count
            else:
                entry["computing"] += count
        with self.lock:
            self.running = (now, snapshot)
        return snapshot


MODEL = Model()


def gpu_name(node: dict) -> str | None:
    try:
        devices = json.loads(node.get("gpus_json") or "[]")
    except (ValueError, AttributeError, TypeError):
        return None
    return devices[0].get("name") if devices else None


def completion(node: dict, root: str, running: dict[str, dict[str, int]], model: Model = MODEL) -> float | None:
    """Seconds until `node` would finish one more tile of `root` if it took it now; None if unknown."""
    kernel = model.kernels.get((node["node_name"], root))
    if kernel is None:
        # No history on this field yet: assume the field's median machine. Leaving such machines
        # out of the comparison let the one with history take every ready tile into its own GPU
        # queue while the others idled (151^3 on dp-107, 2026-10-09: tiles waited 25 min each).
        # The same GPU model's median on this field if one has history, else the slowest machine's:
        # the plain field median was merlin's (24 of 151^3's tiles), so an idle P600 looked as fast
        # as a 3060 and took critical tiles it then held 40+ minutes (2026-10-09 11:10).
        known = {name: value for (name, key), value in model.kernels.items() if key == root}
        if not known:
            return None
        model_name = gpu_name(node)
        same = [value for name, value in known.items() if model_name and model.gpu_names.get(name) == model_name]
        kernel = statistics.median(same) if same else max(known.values())
    overheads = list(model.overheads.values())
    default = ((statistics.median(item[0] for item in overheads), statistics.median(item[1] for item in overheads))
               if overheads else (0.0, 0.0))
    before, after = model.overheads.get(node["node_name"], default)
    state = running.get(node["node_name"], {"before": 0, "computing": 0, "total": 0})
    backlog = (state["before"] + 0.5 * state["computing"]) * kernel
    try:
        slots = len(json.loads(node.get("slots_json") or "[]")) or 1
    except ValueError:
        slots = 1
    waiting_for_slot = kernel + after if state["total"] >= slots else 0.0
    return waiting_for_slot + max(before, backlog) + kernel + after


def withhold(connection, node: dict, specification: dict, candidates: list[dict], now: float,
             model: Model = MODEL) -> bool:
    """True if this critical tile should wait for a machine that would finish it sooner.

    candidates: the other live GPU machines (node rows) that could take it.
    """
    position = tile_of(specification)
    if position is None:
        return False
    root, row, column = position
    progress = model.field(connection, root, now)
    critical = progress["unfinished"] <= TAIL_TILES or (
        progress["lowest"] is not None and row + column < progress["lowest"] + CRITICAL_DIAGONALS)
    if not critical:
        return False
    model.service(connection, root, now)
    running = model.running_tiles(connection, now)
    mine = completion(node, root, running, model)
    if mine is None:
        return False   # no history for this machine on this field yet: don't hold anything back
    others = [value for other in candidates if other["node_name"] != node["node_name"]
              for value in [completion(other, root, running, model)] if value is not None]
    if not others:
        return False
    fastest_kernel = min(model.kernels[(name, key)] for (name, key) in model.kernels if key == root)
    sooner = sum(mine > other + fastest_kernel for other in others)
    # Hold it only if the machines that would finish it sooner can also take the field's other
    # ready tiles: one per machine. With 23 ready tiles and one faster GPU (107^3's tail on
    # 2026-10-07: gawain at 30 s a tile, P600s at 48 s), holding each back idled eight GPUs.
    return sooner > 0 and progress.get("ready", 0) <= sooner


def gpu_machines(connection, now: float, lease_seconds: float) -> list[dict[str, Any]]:
    """Live machines that can take DP tiles on a GPU: heartbeating, enabled, not paused, with a free GPU."""
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM nodes n WHERE compute_enabled=1 AND last_heartbeat>? AND gpus_json NOT IN ('','[]') "
        "AND NOT EXISTS (SELECT 1 FROM node_dispatch_pauses pause WHERE pause.node_name=n.node_name)",
        (now - lease_seconds,))]
    held = {row[0] for row in connection.execute(
        "SELECT DISTINCT node_name FROM runs WHERE state='running' AND gpu_index IS NOT NULL")}
    return [row for row in rows if row["node_name"] not in held]
