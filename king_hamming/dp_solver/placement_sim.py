#!/usr/bin/env python3
"""Replay a distributed DP field under different tile placement policies (no live cluster).

Step 1 of docs/GPU_TILE_PLACEMENT_PLAN.md. A discrete-event simulation of the tile DAG on the
fleet as it was: each machine's slots and its one GPU (kernels run one at a time, in order of
arrival), every tile's fetch, kernel, pack and publish times taken from what the live campaign
recorded, and the kernel time moved between GPU classes by their measured speed ratio.

Policies (each adds to the one before it):
  today     - the leader's order: smaller (estimated cheaper) tiles first, then the longest ready
  critical  - lowest anti-diagonal first, then most blocked successors, then the longest ready
  cap       - critical, and at most `cap` tiles in flight per machine (P600s stop hoarding)
  ect       - what cluster/placement.py does (with --cap at least the slot count, no cap): order by
              anti-diagonal, and a critical tile (on the 3 lowest unfinished diagonals, or any tile
              in the last ~820) goes to a machine only if it would finish it no later than the
              fastest machine could, plus one fast kernel of slack ("leave it for a faster GPU")

Example:
  python3 dp_solver/placement_sim.py --state cluster/deployments/continuous-campaign --field 31^7 \\
      --start "2026-10-05 17:40" --gpu-free dp-151="2026-10-05 20:12"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import heapq
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dp_solver.tiles import dependencies, tile  # noqa: E402

POLICIES = ("today", "critical", "cap", "ect")


@dataclass
class Machine:
    name: str
    gpu_class: str
    slots: int
    pre: float               # fetch + halo before the kernel (median of its own tiles)
    post: float              # pack + publish + leader round trips after it
    speed: float             # kernel seconds per unit of work (merlin's 3060 = its own median)
    gpu_free_at: float = 0.0  # the GPU is held (e.g. by a matching) until then
    in_flight: int = 0
    gpu_busy_until: float = 0.0
    gpu_seconds: float = 0.0


@dataclass
class Field:
    rows: int
    columns: int
    work: dict               # (row, column) -> kernel work, in units of the reference GPU's seconds
    predecessors: dict       # (row, column) -> list of (row, column)
    successors: dict
    done: set                # tiles complete before the simulation starts


def order_key(policy: str, key, ready_at: float, field_: Field, remaining_predecessors: dict):
    if policy == "today":
        return (field_.work[key], ready_at)
    if policy == "ect":   # as cluster/placement.py: anti-diagonal, then age
        return (key[0] + key[1], ready_at)
    blocked = sum(1 for successor in field_.successors[key] if remaining_predecessors[successor] == 1)
    return (key[0] + key[1], -blocked, ready_at)


def simulate(field_: Field, machines: list[Machine], policy: str, cap: int = 2, critical_diagonals: int = 3,
             schedule_delay: float = 1.5, seed: int = 1) -> dict:
    """Run one policy to completion; return the makespan, the tail and per-class GPU use (seconds from start)."""
    rng = random.Random(seed)
    remaining = {key: sum(1 for item in field_.predecessors[key] if item not in field_.done)
                 for key in field_.work if key not in field_.done}
    ready: dict = {key: 0.0 for key, count in remaining.items() if count == 0}
    unfinished_by_diagonal: dict[int, int] = {}
    for key in remaining:
        unfinished_by_diagonal[key[0] + key[1]] = unfinished_by_diagonal.get(key[0] + key[1], 0) + 1
    total = len(remaining)
    tail_count = sum(min(d + 1, 40) for d in range(40))  # about the last 40 anti-diagonals
    events: list = []   # (time, sequence, kind, payload)
    sequence = 0
    now = 0.0
    finished = 0
    tail_start = None
    in_flight: dict = {}  # key -> (machine, kernel seconds)

    def push(at, kind, payload):
        nonlocal sequence
        sequence += 1
        heapq.heappush(events, (at, sequence, kind, payload))

    def kernel(machine: Machine, key) -> float:
        return field_.work[key] * machine.speed

    def lowest_diagonals() -> set:
        live = sorted(d for d, count in unfinished_by_diagonal.items() if count)
        return set(live[:critical_diagonals])

    def finish_estimate(machine: Machine, key) -> float:
        """When this machine would finish the tile if it took it now (inf if it has no free slot)."""
        if machine.in_flight >= (min(cap, machine.slots) if policy in ("cap", "ect") else machine.slots):
            return float("inf")
        start = max(now + machine.pre, machine.gpu_free_at, machine.gpu_busy_until)
        return start + kernel(machine, key) + machine.post

    def dispatch():
        order = machines[:]
        rng.shuffle(order)   # agents ask in no particular order
        while True:
            progressed = False
            tail = total - finished <= 820   # cluster/placement.py TAIL_TILES
            lows = lowest_diagonals() if policy == "ect" else set()
            ranked = sorted(ready, key=lambda key: order_key(policy, key, ready[key], field_, remaining)) \
                if ready else []
            for machine in order:
                limit = min(cap, machine.slots) if policy in ("cap", "ect") else machine.slots
                if machine.in_flight >= limit or not ranked:
                    continue
                choice = None
                for key in ranked[:100]:
                    if policy == "ect" and (tail or key[0] + key[1] in lows):
                        mine = finish_estimate(machine, key)
                        best = min(finish_estimate(other, key) for other in machines)
                        slack = min(kernel(other, key) for other in machines)
                        if mine > best + slack:
                            continue   # a faster GPU will ask within seconds
                    choice = key
                    break
                if choice is None:
                    continue
                ranked.remove(choice)
                del ready[choice]
                machine.in_flight += 1
                push(now + machine.pre, "gpu", (machine.name, choice))
                progressed = True
            if not progressed:
                return

    by_name = {machine.name: machine for machine in machines}
    dispatch()
    while events:
        now, _, kind, payload = heapq.heappop(events)
        if kind == "gpu":
            name, key = payload
            machine = by_name[name]
            seconds = kernel(machine, key)
            start = max(now, machine.gpu_free_at, machine.gpu_busy_until)
            machine.gpu_busy_until = start + seconds
            machine.gpu_seconds += seconds
            push(machine.gpu_busy_until + machine.post, "done", (name, key))
        elif kind == "done":
            name, key = payload
            machine = by_name[name]
            machine.in_flight -= 1
            finished += 1
            unfinished_by_diagonal[key[0] + key[1]] -= 1
            if tail_start is None and total - finished <= tail_count:
                tail_start = now
            for successor in field_.successors[key]:
                if successor in remaining:
                    remaining[successor] -= 1
                    if remaining[successor] == 0:
                        push(now + schedule_delay, "ready", successor)
            dispatch()
        elif kind == "ready":
            ready[payload] = now
            dispatch()
    makespan = now
    use = {}
    for machine in machines:
        available = max(0.0, makespan - machine.gpu_free_at)
        use.setdefault(machine.gpu_class, []).append(machine.gpu_seconds / available if available else 0.0)
    return {"policy": policy, "tiles": total, "hours": makespan / 3600,
            "tail_minutes": (makespan - (tail_start or makespan)) / 60,
            "gpu_use": {name: round(statistics.mean(values), 3) for name, values in use.items()}}


# ----- building the replay from the leader database ----------------------------------------------

GPU_CLASS = {"dp-151": "RTX 3060 Laptop", "dp-156": "T1000"}


def load(state: Path, field_name: str, start: float, gpu_free: dict[str, float]) -> tuple[Field, list[Machine]]:
    """The field's tile DAG and the fleet as the live campaign recorded them."""
    p, r = map(int, field_name.split("^"))
    connection = sqlite3.connect(f"file:{state / 'leader.sqlite'}?mode=ro", uri=True)
    root, specification = connection.execute(
        "SELECT run_id,specification FROM runs WHERE parent_run_id IS NULL AND json_extract(specification,"
        "'$.program')='dp_distributed' AND json_extract(specification,'$.arguments.p')=? AND "
        "json_extract(specification,'$.arguments.r')=? AND state='complete' ORDER BY created DESC",
        (p, r)).fetchone()
    side = json.loads(specification)["arguments"]["tile_side"]
    records = {}
    for row, column, node, started, finished, details in connection.execute(
            "SELECT json_extract(specification,'$.arguments.row'),json_extract(specification,'$.arguments.column'),"
            "node_name,started,finished,progress_details FROM runs WHERE parent_run_id=? AND state='complete'",
            (root,)):
        values = json.loads(details or "{}")
        current = records.get((row, column))
        if current is None or finished < current["finished"]:
            records[(row, column)] = {"node": node, "started": started, "finished": finished, **values}
    rows = 1 + max(key[0] for key in records)
    columns = 1 + max(key[1] for key in records)
    # Per-machine overheads and GPU speed from its own interior tiles (full size) in the window.
    interior = {key for key in records if key[0] < rows - 1 and key[1] < columns - 1}
    per_node: dict[str, dict[str, list]] = {}
    for key in interior:
        record = records[key]
        if record.get("engine") != "gpu" or record["finished"] < start or "kernel_seconds" not in record:
            continue
        lists = per_node.setdefault(record["node"], {"kernel": [], "pre": [], "post": []})
        lists["kernel"].append(record["kernel_seconds"])
        lists["pre"].append(record.get("fetch_seconds", 0) + record.get("halo_seconds", 0))
        busy = record.get("fetch_seconds", 0) + record.get("halo_seconds", 0) + record.get("gpu_wait_seconds", 0) + \
            record["kernel_seconds"]
        lists["post"].append(max(0.0, record["finished"] - record["started"] - busy))
    reference = statistics.median(per_node["dp-151"]["kernel"])
    machines = []
    for node, lists in sorted(per_node.items()):
        if len(lists["kernel"]) < 20:
            continue
        slots = max_concurrent([(records[k]["started"], records[k]["finished"]) for k in records
                                if records[k]["node"] == node and records[k]["finished"] >= start])
        machines.append(Machine(node, GPU_CLASS.get(node, "P600"), slots, statistics.median(lists["pre"]),
                                statistics.median(lists["post"]), statistics.median(lists["kernel"]) / reference,
                                gpu_free_at=max(0.0, gpu_free.get(node, start) - start)))
    speed = {machine.name: machine.speed for machine in machines}
    work = {}
    for key, record in records.items():
        if record.get("engine") == "gpu" and record["node"] in speed and "kernel_seconds" in record:
            work[key] = record["kernel_seconds"] / speed[record["node"]]
        else:  # a CPU tile or an unknown machine: scale the reference by the tile's area
            shape = tile(p, r, side, *key)
            area = (shape.last_u - shape.first_u + 1) * (shape.last_v - shape.first_v + 1)
            work[key] = reference * area / side**2
    predecessors = {key: [(item.row, item.column) for item in dependencies(p, r, side, tile(p, r, side, *key))]
                    for key in records}
    successors: dict = {key: [] for key in records}
    for key, items in predecessors.items():
        for item in items:
            successors[item].append(key)
    done = {key for key, record in records.items() if record["finished"] < start}
    return Field(rows, columns, work, predecessors, successors, done), machines


def max_concurrent(intervals: list[tuple[float, float]]) -> int:
    edges = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals])
    current = best = 0
    for _, step in edges:
        current += step
        best = max(best, current)
    return max(1, best)


def parse_time(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--field", required=True, help="e.g. 31^7")
    parser.add_argument("--start", required=True, help="replay from this local time, e.g. '2026-10-05 17:40'")
    parser.add_argument("--gpu-free", action="append", default=[], help="NODE='YYYY-MM-DD HH:MM': GPU held until then")
    parser.add_argument("--cap", type=int, default=2, help="in-flight tiles per machine for cap/ect (at most its slots)")
    parser.add_argument("--seeds", type=int, default=3)
    arguments = parser.parse_args()
    start = parse_time(arguments.start)
    gpu_free = {item.split("=", 1)[0]: parse_time(item.split("=", 1)[1]) for item in arguments.gpu_free}
    field_, machines = load(arguments.state, arguments.field, start, gpu_free)
    print(f"{arguments.field}: {len(field_.work) - len(field_.done):,} tiles left at {arguments.start}; machines: " +
          ", ".join(f"{m.name}({m.gpu_class}, {m.slots} slots, kernel x{m.speed:.1f}, pre {m.pre:.1f}s, post {m.post:.1f}s)"
                    for m in machines))
    for policy in POLICIES:
        results = [simulate(field_, [Machine(**m.__dict__) for m in machines], policy,
                            cap=arguments.cap, seed=seed) for seed in range(arguments.seeds)]
        hours = statistics.mean(item["hours"] for item in results)
        tail = statistics.mean(item["tail_minutes"] for item in results)
        print(f"  {policy:9s} {hours:6.2f} h   tail {tail:6.1f} min   GPU use {results[0]['gpu_use']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
