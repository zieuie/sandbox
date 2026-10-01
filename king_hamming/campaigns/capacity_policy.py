"""Memory-first matching policy; no process launch or cluster mutation here."""
from __future__ import annotations

from cluster.resources import HOST_RESERVE_BYTES, cpu_count
from matching_solver.artifacts import request_count
from matching_solver_multi.resources import partitioned_memory

NAME = "capacity"
DEFAULTS = {"memory_margin_percent": 25, "partitioned_batch": 65536}
MIB = 1024**2


def verifier_memory(dp: dict) -> int:
    """Mirror the streaming certificate verifier's peak admission arithmetic."""
    return 4 * dp["q"] + 4 * dp["budget"] + 2 * ((dp["q"] + 7) // 8) + 64 * MIB


def single_memory(dp: dict, threads: int) -> int:
    """Mirror kh_match_kernel's state/field peak, including native thread stacks."""
    q, n = dp["q"], request_count(dp)
    # uint64 first, two uint32 fields, uint16 stripes, ABI tail padding.
    descriptors = 24 * len(dp["runs"])
    common = descriptors + 8 * MIB * threads + 64 * MIB
    state = 8 * q + 24 * n + (q + 7) // 8 + common
    if threads > 1:
        state += 4 * n + 8 * ((q + 63) // 64)
    field = 4 * q + 4 * dp["budget"] * threads + common
    return max(state, field, verifier_memory(dp))


def validate(settings: dict) -> None:
    margin, batch = settings.get("memory_margin_percent"), settings.get("partitioned_batch")
    if type(margin) is not int or not 10 <= margin <= 100:
        raise ValueError("memory margin must be an integer from 10 to 100 percent")
    if type(batch) is not int or not 1 <= batch <= 1048576:
        raise ValueError("partitioned batch must be 1..1048576")
    if not 2 <= settings["matching_workers"] <= 16:
        raise ValueError("partitioned planning supports 2..16 machines")


class CapacityPolicy:
    """Prefer one eligible host, then the smallest RAM-feasible owner group.

    Busy nodes remain candidates: temporary occupancy must not turn a fitting
    single-host problem into a distributed problem. Unknown RAM is not capacity.
    Host names are planning hints, not reservations; the scheduler rechecks RAM.
    """

    def plan(self, dp: dict, settings: dict, nodes: list[dict]) -> dict:
        def blocked(reason, **extra):
            return dict(admitted=False, reason=reason, **extra)

        if dp["q"] > settings["max_field_elements"]:
            return blocked("field limit")
        if request_count(dp) * dp["f"] > settings["max_matching_edges"]:
            return blocked("edge limit")
        candidates = []
        for node in nodes:
            if node.get("state") != "healthy" or not node.get("compute_enabled", True):
                continue
            ram, cpus = int(node.get("memory_bytes") or 0), cpu_count(node.get("cpu_set") or "")
            usable = min(settings["max_matching_bytes"], max(0, ram - HOST_RESERVE_BYTES))
            if usable and cpus:
                candidates.append(dict(name=node["node_name"], memory=usable, cpus=cpus))
        candidates.sort(key=lambda n: (-n["memory"], -n["cpus"], n["name"]))
        if not candidates:
            return blocked("waiting for measured healthy compute capacity")

        def margin(value):
            return (value * (100 + settings["memory_margin_percent"]) + 99) // 100

        # Try fewer threads before adding network participants: thread scratch
        # must not force a split when the same native matcher fits with one.
        for threads in range(min(settings["matching_threads"], max(n["cpus"] for n in candidates)), 0, -1):
            memory = margin(single_memory(dp, threads))
            hosts = [n for n in candidates if n["cpus"] >= threads and n["memory"] >= memory]
            if hosts:
                return dict(admitted=True, reason="admitted: existing single-host matcher",
                            program="match", workers=1, threads=threads, max_bytes=memory,
                            coordinator_memory_bytes=memory, worker_memory_bytes=0,
                            hosts=[hosts[0]["name"]], memory_margin_percent=settings["memory_margin_percent"])

        for workers in range(2, min(len(candidates), settings["matching_workers"]) + 1):
            for threads in range(min(settings["matching_threads"], 64,
                                     max(n["cpus"] for n in candidates)), 0, -1):
                coordinator, worker = partitioned_memory(dp, workers, threads, settings["partitioned_batch"])
                coordinator, worker = margin(coordinator), margin(worker)
                eligible = [n for n in candidates if n["cpus"] >= threads and n["memory"] >= worker]
                leaders = [n for n in eligible if n["memory"] >= coordinator]
                if leaders and len(eligible) >= workers:
                    leader = leaders[0]
                    selected = [leader] + [n for n in eligible if n != leader][:workers - 1]
                    return dict(admitted=True, reason="admitted: partitioned memory fallback",
                                   program="match_partitioned", workers=workers, threads=threads,
                                   coordinator_memory_bytes=coordinator, worker_memory_bytes=worker,
                                   hosts=[n["name"] for n in selected], batch=settings["partitioned_batch"],
                                   memory_margin_percent=settings["memory_margin_percent"],
                                   capacity_feasible=True)
        return blocked("insufficient per-machine memory for any permitted group")
