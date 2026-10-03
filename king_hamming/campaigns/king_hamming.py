#!/usr/bin/env python3
"""King Hamming policy: continuously feed retained DP into distributed matching."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from dp_solver import scheduling
from dp_solver.artifacts import decode_dp
from dp_solver.launch_dp import request, save
from artifact_resolver import retrieve
from matching_solver.artifacts import load_dp, request_count, verify
from matching_solver.adapter import distributed_memory_required
from matching_solver.polynomials import first_primitive, next_primitive
from matching_solver.submit import specification as matching_specification
from campaigns import gpu_policy

TERMINAL = {"complete", "failed", "cancelled"}
DEFAULTS = {
    "target_dp_roots": 2, "max_ready_fields": 4, "max_dp_attempts": 3,
    "max_dp_roots": 8, "target_ready_dp_tiles": 18,
    "max_matching_attempts": 3, "matching_retry_seconds": 300,
    "max_state_bytes": 16 * 1024**3, "max_visits": 30_000_000_000_000,
    "frontier_max_prime": 19, "frontier_max_exponent": 11,
    "frontier_max_visits": 200_000_000_000_000,
    "dp_threads": 16, "tile_side": 512, "max_tile_bytes": 2 * 1024**3,
    "matching_workers": 9, "matching_threads": 16,
    "matching_small_workers": 2, "matching_medium_workers": 4,
    "matching_small_requests": 1_000_000,
    "matching_medium_requests": 10_000_000,
    "max_matching_bytes": 6 * 1024**3,
    "max_matching_edges": 1_000_000_000_000_000,
    "max_field_elements": 100_000_000, "matching_priority": 100,
    "minimum_free_bytes": 20 * 1024**3,
}


def durable_work(run: dict | None) -> tuple[int, int, int, float]:
    """Return a stable ordering key for retained progress in one attempt."""
    if run is None:
        return (-1, -1, -1, -1.0)
    return (
        int(run.get("_tile_complete", 0)),
        int(run.get("progress_checkpoint_done", 0) or run.get("restored_done", 0)),
        int(run.get("progress_done", 0)),
        float(run.get("created", 0)),
    )


def preferred_attempt(entries: list[dict], runs: dict[str, dict],
                      prefer_complete: bool = True) -> tuple[dict, dict] | None:
    """Prefer success, then the nonterminal attempt with most durable work, then newest history."""
    available = [(index, entry, runs.get(entry["run_id"]))
                 for index, entry in enumerate(entries)
                 if runs.get(entry["run_id"]) is not None]
    if not available:
        return None
    complete = [item for item in available if item[2]["state"] == "complete"]
    if prefer_complete and complete:
        _, entry, run = max(complete, key=lambda item: (durable_work(item[2]), item[0]))
        return entry, run
    active = [item for item in available if item[2]["state"] not in TERMINAL]
    pool = active or available
    _, entry, run = max(pool, key=lambda item: (durable_work(item[2]), item[0]))
    return entry, run


def atomic_save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def run_rows(state: Path, leader: str, required_capability=None) -> tuple[dict[str, dict], list[dict]]:
    """Return all roots plus nodes, bypassing the public status row limit."""
    status = request(leader, "/v1/status")
    required = ([required_capability] if isinstance(required_capability, str) else required_capability) or []
    if not set(required) <= set(status.get("capabilities", [])):
        raise RuntimeError("capacity campaign requires an upgraded leader with capacity admission and partitioned matching")
    database = state / "leader.sqlite"
    if not database.exists():
        return ({row["run_id"]: row for row in status["runs"]
                 if row.get("parent_run_id") is None}, status["nodes"])
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM runs WHERE parent_run_id IS NULL").fetchall()
        result = {row["run_id"]: dict(row) for row in rows}
        for row in connection.execute(
            "SELECT parent_run_id,state,COUNT(*) AS count FROM runs "
            "WHERE parent_run_id IS NOT NULL GROUP BY parent_run_id,state"
        ):
            parent = result.get(row["parent_run_id"])
            if parent is not None:
                parent[f"_tile_{row['state']}"] = row["count"]
        for parent in result.values():
            parent["_tile_metrics"] = True
            parent["_ready_tiles"] = (parent.get("_tile_queued", 0) +
                                       parent.get("_tile_running", 0))
        return result, status["nodes"]


def collect_dp(state: Path, manifest: dict, runs: dict[str, dict], pipeline: dict,
               nodes: list[dict] | tuple = ()) -> int:
    """Collect successful KHD1 roots while retaining every attempt's lineage."""
    collected, entries_by_field = 0, {}
    for entry in manifest["entries"]:
        spec = entry["specification"]
        if spec.get("program") != "dp_distributed":
            continue
        arguments = spec["arguments"]
        field = f"{arguments['p']}^{arguments['r']}"
        record = pipeline["fields"].setdefault(field, {
            "p": arguments["p"], "r": arguments["r"],
            "matching_attempts": [],
        })
        run = runs.get(entry["run_id"])
        attempts = record.setdefault("dp_attempts", [])
        attempt = next((item for item in attempts
                        if item["run_id"] == entry["run_id"]), None)
        if attempt is None:
            attempt = {"run_id": entry["run_id"]}
            attempts.append(attempt)
        if run is not None:
            attempt["state"] = run["state"]
        entries_by_field.setdefault(field, []).append((entry, run))

    for field, entries in entries_by_field.items():
        record = pipeline["fields"][field]
        current = preferred_attempt([entry for entry, _ in entries], runs,
                                    prefer_complete=False)
        if current is not None:
            entry, run = current
            record.update(dp_current_run_id=entry["run_id"],
                          dp_current_state=run["state"])
        if record.get("dp_artifact"):
            record["dp_state"] = "complete"
            continue
        successful = next(((entry, run) for entry, run in reversed(entries)
                           if run is not None and run["state"] == "complete"), None)
        if successful is None:
            record["dp_state"] = record.get("dp_current_state", "unknown")
            continue
        entry, run = successful
        output = state / "results" / f"{record['p']}_{record['r']}_{entry['run_id']}.khdp"
        raw = retrieve(run, output, 16 * 1024**2, nodes)
        dp = decode_dp(raw)
        if (dp["p"], dp["r"]) != (record["p"], record["r"]):
            raise ValueError("collected DP dimensions differ from queue specification")
        count = request_count(dp)
        record.update(dp_run_id=entry["run_id"], dp_state="complete",
                      dp_artifact=str(output),
                      dp_sha256=hashlib.sha256(raw).hexdigest(), q=dp["q"],
                      requests=count, edges=count * dp["f"])
        collected += 1
    return collected


def matching_admitted(dp: dict, settings: dict) -> tuple[bool, str]:
    edges = request_count(dp) * dp["f"]
    workers = matching_worker_count(dp, settings)
    coordinator, shard = distributed_memory_required(
        dp, workers, settings["matching_threads"])
    if dp["q"] > settings["max_field_elements"]:
        return False, "field limit"
    if edges > settings["max_matching_edges"]:
        return False, "edge limit"
    if max(coordinator, shard) > settings["max_matching_bytes"]:
        return False, "memory limit"
    return True, "admitted"


def plan_matching(dp: dict, settings: dict, nodes, policy=None) -> dict | None:
    """Prefer one fenced GPU when a live node can hold the field; else the configured policy."""
    gpu = gpu_policy.plan(dp, settings, list(nodes))
    if gpu is not None:
        return gpu
    return policy.plan(dp, settings, nodes) if policy is not None else None


def matching_worker_count(dp: dict, settings: dict) -> int:
    """Choose a configured small, medium, or full group by retained request count."""
    count = request_count(dp)
    if count <= settings["matching_small_requests"]:
        return min(settings["matching_workers"], settings["matching_small_workers"])
    if count <= settings["matching_medium_requests"]:
        return min(settings["matching_workers"], settings["matching_medium_workers"])
    return settings["matching_workers"]


def matchable_backlog(pipeline: dict, nodes=(), policy=None) -> int:
    """Count retained DP artifacts that can currently enter matching.

    DP results outside the matching limits are useful retained work, not queue
    pressure: a later, larger cluster or matching implementation may consume
    them without recomputing the table.
    """
    settings = pipeline["settings"]
    ready = 0
    for record in pipeline["fields"].values():
        if not record.get("dp_artifact") or record.get("matching_complete"):
            continue
        dp, _ = load_dp(Path(record["dp_artifact"]))
        plan = plan_matching(dp, settings, nodes, policy)
        if plan is None:
            admitted, reason = matching_admitted(dp, settings)
        else:
            record["matching_plan"] = plan
            admitted, reason = plan["admitted"], plan["reason"]
        record["matching_admission"] = reason
        if admitted:
            ready += 1
    return ready


def archive_matching(state: Path, record: dict, attempt: dict, run: dict,
                     settings: dict, nodes: list[dict] | tuple = ()) -> Path:
    """Download and independently verify a KHM1 certificate."""
    dp, digest = load_dp(Path(record["dp_artifact"]))
    count = request_count(dp)
    maximum = (count * (dp["f"] + 1).bit_length() + 7) // 8 + (count + 7) // 8 + 4096
    output = state / "matching-results" / f"{record['p']}_{record['r']}_{run['run_id']}.khmatch"
    retrieve(run, output, maximum, nodes)
    summary = verify(output, dp, digest, settings["max_matching_bytes"])
    if summary["polynomial"] != attempt["poly"]:
        raise ValueError("archived certificate polynomial differs from queued attempt")
    return output


def enqueue_attempt(manifest: dict, pipeline: dict, record: dict,
                    polynomial: list[int], rerun: bool = False, plan=None) -> None:
    settings = pipeline["settings"]
    dp, _ = load_dp(Path(record["dp_artifact"]))
    workers = matching_worker_count(dp, settings) if plan is None else plan["workers"]
    if plan is not None and (not plan["admitted"] or plan["program"] not in {"match", "match_partitioned", "match_gpu", "match_gpu_blocks"}):
        raise ValueError("capacity policy cannot submit an inadmissible plan")
    if plan is not None and plan["program"] == "match_gpu":
        from gpu_match_solver.submit import specification as gpu_specification
        job = gpu_specification(Path(record["dp_artifact"]), ",".join(map(str, polynomial)),
                                plan["threads"], plan["max_bytes"])
    elif plan is not None and plan["program"] == "match_gpu_blocks":
        from gpu_block_match_solver.submit import specification as block_specification
        job = block_specification(Path(record["dp_artifact"]), ",".join(map(str, polynomial)),
                                  plan["threads"], plan["max_bytes"], plan["gpu_memory_bytes"])
    elif plan is not None and plan["program"] == "match_partitioned":
        from matching_solver_multi.submit import specification as partitioned_specification
        job = partitioned_specification(
            Path(record["dp_artifact"]), ",".join(map(str, polynomial)), workers=workers,
            threads=plan["threads"], batch=plan["batch"], margin=settings["memory_margin_percent"],
            max_bytes=settings["max_matching_bytes"], max_edges=settings["max_matching_edges"],
            max_field_elements=settings["max_field_elements"])
    else:
        job = matching_specification(
            Path(record["dp_artifact"]), ",".join(map(str, polynomial)),
            settings["matching_threads"] if plan is None else plan["threads"],
            settings["max_matching_bytes"] if plan is None else plan["max_bytes"],
            distributed=plan is None, max_edges=settings["max_matching_edges"],
            max_field_elements=settings["max_field_elements"], workers=workers)
    if plan is not None:
        job["arguments"]["require_known_capacity"] = True
    payload = {"specification": job,
               "priority": settings["matching_priority"]}
    if rerun:
        payload["rerun"] = True
    result = request(manifest["leader"], "/v1/enqueue", payload)
    attempt = {"poly": polynomial, **result}
    if plan is not None:
        attempt["plan"] = plan
    record["matching_attempts"].append(attempt)


def recover_matching_attempts(pipeline: dict, runs: dict[str, dict]) -> None:
    """Recover enqueue replies lost before a capacity feeder saved its state.

    Capacity-dependent thread/memory settings change the queue specification hash.
    Reattach existing exact DP/polynomial attempts before considering a new plan.
    """
    by_digest = {}
    for record in pipeline["fields"].values():
        if record.get("dp_artifact") and not record.get("matching_complete"):
            _, digest = load_dp(Path(record["dp_artifact"]))
            by_digest[digest.hex()] = record
    for run_id, run in sorted(runs.items(), key=lambda item: float(item[1].get("created") or 0)):
        specification = run.get("specification")
        if isinstance(specification, str):
            specification = json.loads(specification)
        if not isinstance(specification, dict) or specification.get("program") not in {"match", "match_distributed", "match_partitioned", "match_gpu", "match_gpu_blocks"}:
            continue
        arguments = specification["arguments"]
        record = by_digest.get(arguments.get("dp_sha256"))
        if record is None or any(attempt["run_id"] == run_id for attempt in record["matching_attempts"]):
            continue
        record["matching_attempts"].append({"run_id": run_id, "poly": arguments["poly"],
                                           "state": run["state"], "recovered": True})


def advance_matching(state: Path, manifest: dict, runs: dict[str, dict],
                     nodes: list[dict], pipeline: dict, policy=None) -> dict[str, int]:
    """Archive outcomes, retry obstructions, and submit ready fields."""
    counts, settings = Counter(), pipeline["settings"]
    if policy is not None:
        recover_matching_attempts(pipeline, runs)
    live_nodes = sum(bool(node.get("compute_enabled", True)) and
                     node.get("state", "healthy") == "healthy" for node in nodes)
    records = sorted(pipeline["fields"].values(), key=lambda item: item.get("edges", 2**63))
    for record in records:
        if not record.get("dp_artifact") or record.get("matching_complete"):
            continue
        dp, _ = load_dp(Path(record["dp_artifact"]))
        # Collect existing outcomes even when capacity or admission changed.
        # A healthy result must not disappear behind a new resource limit.
        polynomial, rerun = None, False
        attempts = record["matching_attempts"]
        if attempts:
            for attempt in attempts:
                if attempt["run_id"] in runs:
                    attempt["state"] = runs[attempt["run_id"]]["state"]
            selected = preferred_attempt(attempts, runs)
            if selected is None:
                continue
            latest, run = selected
            latest["state"] = run["state"]
            if run["state"] == "failed" and "block matching incomplete" in (run.get("error") or ""):
                # The block exchange can end short for one field and still succeed for another
                # primitive polynomial, so move on rather than rerun the same deterministic attempt.
                latest["incomplete"] = True
                if sum(bool(item.get("incomplete")) for item in attempts) >= settings["max_matching_attempts"]:
                    record["matching_failure"] = run.get("error") or run["state"]
                    counts["failed_terminal"] += 1
                    continue
                polynomial = next_primitive(record["p"], record["r"], record["q"], latest["poly"])
                if polynomial is None:
                    record["candidate_exhausted"] = True
                    counts["exhausted"] += 1
                    continue
            elif run["state"] in {"failed", "cancelled"}:
                same_candidate_failures = sum(
                    item.get("poly") == latest["poly"] and
                    item.get("state") in {"failed", "cancelled"}
                    for item in attempts
                )
                if same_candidate_failures >= settings["max_matching_attempts"]:
                    record["matching_failure"] = run.get("error") or run["state"]
                    counts["failed_terminal"] += 1
                    continue
                finished = float(run.get("finished") or 0)
                if finished and time.time() - finished < settings["matching_retry_seconds"]:
                    counts["retry_wait"] += 1
                    continue
                polynomial, rerun = latest["poly"], True
            elif run["state"] != "complete":
                continue
            else:
                if not latest.get("archive"):
                    latest["archive"] = str(archive_matching(
                        state, record, latest, run, settings, nodes))
                    counts["archived"] += 1
                if run.get("progress_done", 0) >= run.get("progress_total", 0):
                    record["matching_complete"] = True
                    counts["completed"] += 1
                    continue
                polynomial = next_primitive(record["p"], record["r"], record["q"], latest["poly"])
                if polynomial is None:
                    record["candidate_exhausted"] = True
                    counts["exhausted"] += 1
                    continue
        plan = plan_matching(dp, settings, nodes, policy)
        if plan is None:
            admitted, reason = matching_admitted(dp, settings)
        else:
            record["matching_plan"] = plan
            admitted, reason = plan["admitted"], plan["reason"]
        record["matching_admission"] = reason
        if not admitted:
            counts["inadmissible"] += 1
            continue
        if polynomial is None:
            polynomial = first_primitive(record["p"], record["r"], record["q"])
        required_workers = matching_worker_count(dp, settings) if plan is None else plan["workers"]
        if live_nodes < required_workers:
            record["matching_admission"] = "waiting for nodes"
            counts["waiting_nodes"] += 1
            continue
        enqueue_attempt(manifest, pipeline, record, polynomial, rerun=rerun, plan=plan)
        counts["retried" if rerun else "submitted"] += 1
    return dict(counts)


def replenish_dp(state: Path, manifest: dict, runs: dict[str, dict], pipeline: dict,
                 nodes: list[dict] | tuple = (), policy=None) -> int:
    """Keep a bounded root backlog while respecting disk and matchable pressure."""
    settings = pipeline["settings"]
    if shutil.disk_usage(state).free < settings["minimum_free_bytes"]:
        pipeline["feeder_state"] = "local disk watermark"
        return 0
    ready = matchable_backlog(pipeline, nodes, policy)
    if ready >= settings["max_ready_fields"]:
        pipeline["feeder_state"] = "matching backpressure"
        return 0
    active, attempts, ready_tiles, tile_metrics = 0, {}, 0, False
    for entry in manifest["entries"]:
        arguments = entry["specification"]["arguments"]
        field = (arguments["p"], arguments["r"])
        attempts.setdefault(field, []).append(entry)
        run = runs.get(entry["run_id"])
        if run is not None and run["state"] not in TERMINAL:
            active += 1
            ready_tiles += int(run.get("_ready_tiles", 0))
            tile_metrics = tile_metrics or bool(run.get("_tile_metrics"))
    known = set(attempts)
    healthy = [node for node in nodes if node.get("compute_enabled", True) and
               node.get("state", "healthy") == "healthy"]
    live_compute = len(healthy)
    live_slots = 0
    for node in healthy:
        try:
            slots = json.loads(node.get("slots_json") or "[]")
        except (TypeError, ValueError):
            slots = []
        live_slots += max(1, len(slots))
    dynamic_ready_target = max(settings["target_ready_dp_tiles"], 2 * live_slots)
    needed, added = max(0, settings["target_dp_roots"] - active), 0
    if tile_metrics and ready_tiles < dynamic_ready_target:
        needed = max(needed, min(settings["max_dp_roots"] - active,
                                 dynamic_ready_target - ready_tiles))
    pipeline["demand"] = {
        "live_compute_nodes": live_compute,
        "live_compute_slots": live_slots,
        "active_dp_roots": active,
        "ready_dp_tiles": ready_tiles,
        "ready_tile_target": dynamic_ready_target,
        "roots_requested": needed,
    }
    # Retry transiently failed roots before expanding the mathematical frontier.
    for field, entries in attempts.items():
        if added >= needed:
            break
        selected = preferred_attempt(entries, runs)
        latest_entry, latest = selected if selected is not None else (entries[-1], None)
        record = pipeline["fields"].get(f"{field[0]}^{field[1]}", {})
        if (record.get("dp_artifact") or latest is None or latest["state"] != "failed" or
                len(entries) >= settings["max_dp_attempts"]):
            continue
        specification = json.loads(json.dumps(latest_entry["specification"]))
        result = request(manifest["leader"], "/v1/enqueue", {
            "specification": specification, "rerun": True,
        })
        manifest["entries"].append({"specification": specification, **result})
        save(state / "manifest.json", manifest)
        added += 1
    # Count only fresh, measured worker storage. Reserve the uncompressed
    # remaining bytes of active roots at three replica copies; actual gzip blobs
    # may be smaller, but an optimistic compression ratio is unsafe admission.
    storage = [max(0, int(node.get("storage_free_bytes") or 0) - settings["minimum_free_bytes"])
               for node in healthy]
    projected = 0
    for entry in manifest["entries"]:
        run = runs.get(entry["run_id"])
        if run is None or run["state"] in TERMINAL:
            continue
        estimate = scheduling.dp_estimate(entry["specification"])
        remaining = max(0, int(run.get("progress_total") or 0) - int(run.get("progress_done") or 0))
        total = max(1, int(run.get("progress_total") or 0))
        projected += (3 * estimate["state_bytes"] * remaining + total - 1) // total
    if storage:
        per_host_reserved = (projected + len(storage) - 1) // len(storage)
        storage = [max(0, free - per_host_reserved) for free in storage]
    disk_available = sum(storage)
    pipeline["demand"]["worker_disk_available_bytes"] = disk_available
    disk_blocked = []
    for candidate in scheduling.regional_campaign(
            settings["frontier_max_prime"], settings["frontier_max_exponent"],
            settings["frontier_max_visits"], settings["dp_threads"], settings["max_tile_bytes"]):
        if added >= needed:
            break
        arguments = candidate["arguments"]
        field = (arguments["p"], arguments["r"])
        if field in known:
            continue
        disk_need = 3 * scheduling.dp_estimate(candidate)["state_bytes"]
        balanced_need = (disk_need + len(storage) - 1) // len(storage) if storage else disk_need
        if (len(storage) < 3 or
                min(storage) < max(settings["max_tile_bytes"], balanced_need) or
                disk_need > disk_available):
            if len(disk_blocked) < 8:
                disk_blocked.append(f"{field[0]}^{field[1]}")
            continue
        result = request(manifest["leader"], "/v1/enqueue", {"specification": candidate})
        manifest["entries"].append({"specification": candidate, **result})
        save(state / "manifest.json", manifest)
        known.add(field)
        storage = [free - balanced_need for free in storage]
        disk_available = sum(storage)
        added += 1
    pipeline["demand"]["worker_disk_available_bytes"] = disk_available
    pipeline["demand"]["disk_blocked_fields"] = disk_blocked
    pipeline["feeder_state"] = ("running" if added or active else
                                "waiting for worker disk" if disk_blocked else
                                "regional frontier exhausted")
    return added


def render_status(pipeline: dict, runs: dict[str, dict], nodes: list[dict]) -> str:
    lines = ["# Continuous DP and matching campaign", "",
             f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
             f"Feeder: `{pipeline.get('feeder_state', 'unknown')}`; nodes: {len(nodes)}; "
             f"policy: `{pipeline.get('policy', 'legacy')}`", "",
             ("Demand: " + ", ".join(f"{key}={value}" for key, value in
                                      pipeline.get("demand", {}).items())) if pipeline.get("demand") else "",
             "" if pipeline.get("demand") else "",
             "| Field | DP | Matching | Attempts | Planned engine / machines | Admission |",
             "| --- | --- | --- | ---: | --- | --- |"]
    for field, record in sorted(pipeline["fields"].items(), key=lambda item: item[1].get("edges", 2**63)):
        attempts = record.get("matching_attempts", [])
        matching = "complete" if record.get("matching_complete") else "not queued"
        if attempts:
            selected = preferred_attempt(attempts, runs)
            attempt, run = selected if selected is not None else (attempts[-1], None)
            matching = (run or attempt).get("state", "unknown")
        plan = record.get("matching_plan", {})
        engine = f"{plan['program']} / {plan['workers']}" if plan.get("program") else "—"
        lines.append(f"| {field} | {record.get('dp_state', 'unknown')} | {matching} | {len(attempts)} | {engine} | {record.get('matching_admission', '—')} |")
    return "\n".join(lines) + "\n"


def reconcile(state: Path) -> dict:
    """Perform one restart-safe collect, match, and refill transaction."""
    pipeline_path = state / "pipeline.json"
    with (state / "pipeline.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pipeline = json.loads(pipeline_path.read_text())
        for key, value in DEFAULTS.items():
            pipeline.setdefault("settings", {}).setdefault(key, value)
        validate_settings(pipeline["settings"], pipeline.get("policy", "legacy"))
        policy = matching_policy(pipeline)
        manifest = json.loads((state / "manifest.json").read_text())
        runs, nodes = run_rows(state, manifest["leader"],
                              ["known-capacity-admission-v1", "partitioned-matching-v1"] if policy is not None else None)
        collected = collect_dp(state, manifest, runs, pipeline, nodes)
        matching = advance_matching(state, manifest, runs, nodes, pipeline, policy)
        added = replenish_dp(state, manifest, runs, pipeline, nodes, policy)
        pipeline["last_reconcile"] = time.time()
        atomic_save(pipeline_path, pipeline)
        temporary = state / "PIPELINE_STATUS.tmp"
        temporary.write_text(render_status(pipeline, runs, nodes))
        temporary.replace(state / "PIPELINE_STATUS.md")
        return {"collected_dp": collected, "matching": matching, "added_dp": added,
                "fields": len(pipeline["fields"]), "nodes": len(nodes),
                "feeder": pipeline["feeder_state"]}


def validate_settings(settings: dict, policy_name="legacy") -> None:
    positive = ("target_dp_roots", "max_dp_roots", "target_ready_dp_tiles",
                "max_ready_fields", "max_dp_attempts",
                "max_matching_attempts", "matching_retry_seconds",
                "max_state_bytes", "max_visits", "frontier_max_prime",
                "frontier_max_exponent", "frontier_max_visits",
                "dp_threads", "tile_side", "max_tile_bytes", "matching_workers",
                "matching_small_workers", "matching_medium_workers",
                "matching_small_requests", "matching_medium_requests",
                "matching_threads", "max_matching_bytes", "max_matching_edges",
                "max_field_elements", "minimum_free_bytes")
    if any(type(settings.get(key)) is not int or settings[key] <= 0 for key in positive):
        raise ValueError("pipeline limits must be positive integers")
    if settings["target_dp_roots"] > settings["max_dp_roots"]:
        raise ValueError("target DP roots exceed the hard root cap")
    if not (2 <= settings["frontier_max_prime"] <= 1621 and
            3 <= settings["frontier_max_exponent"] <= 31 and
            settings["frontier_max_exponent"] % 2 == 1 and
            settings["frontier_max_visits"] <= 2**64 - 1):
        raise ValueError("invalid regional DP frontier")
    if not 2 <= settings["matching_workers"] <= 256:
        raise ValueError("distributed matching requires 2-256 workers")
    if not 2 <= settings["matching_small_workers"] <= 256 or \
            not 2 <= settings["matching_medium_workers"] <= 256 or \
            settings["matching_small_requests"] > settings["matching_medium_requests"]:
        raise ValueError("invalid matching group tiers")
    if policy_name == "legacy" and settings["matching_workers"] * settings["matching_threads"] > 256:
        raise ValueError("matching worker/thread product exceeds adapter limit")


def matching_policy(pipeline: dict):
    """Resolve a persisted policy; old deployments retain their existing behavior."""
    name = pipeline.get("policy", "legacy")
    if name == "legacy":
        return None
    if name != "capacity":
        raise ValueError(f"unsupported continuous campaign policy: {name}")
    from campaigns.capacity_policy import CapacityPolicy, DEFAULTS as extra, validate
    for key, value in extra.items():
        pipeline["settings"].setdefault(key, value)
    validate(pipeline["settings"])
    return CapacityPolicy()


def initialize(state: Path, arguments: argparse.Namespace, policy_name="legacy") -> dict:
    """Create pipeline policy beside an existing fresh unified deployment."""
    if not (state / "manifest.json").exists():
        raise ValueError("start the unified cluster deployment before initializing its feeder")
    path = state / "pipeline.json"
    if path.exists():
        raise ValueError("pipeline state already exists")
    settings = {key: getattr(arguments, key) for key in DEFAULTS}
    if policy_name == "capacity":
        from campaigns.capacity_policy import DEFAULTS as extra
        settings.update({key: getattr(arguments, key) for key in extra})
    validate_settings(settings, policy_name)
    pipeline = {"version": 1, "policy": policy_name, "settings": settings, "fields": {},
                "feeder_state": "initialized", "created": time.time()}
    matching_policy(pipeline)
    atomic_save(path, pipeline)
    return pipeline


def adopt_capacity(state: Path) -> dict:
    """Switch a quiescent, upgraded deployment without losing retained work.

    Hold both the feeder lock and the leader's write lock through publication,
    so neither reconciliation nor a racing resume can cross the safety check.
    Existing jobs retain their original solver and checkpoint format.
    """
    with (state / "pipeline.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = json.loads((state / "manifest.json").read_text())
        run_rows(state, manifest["leader"],
                 ["known-capacity-admission-v1", "partitioned-matching-v1"])
        with sqlite3.connect(f"file:{state / 'leader.sqlite'}?mode=rw", uri=True) as database:
            database.execute("BEGIN IMMEDIATE")
            campaign_state = database.execute("SELECT value FROM settings WHERE key='campaign_state'").fetchone()
            active = database.execute("SELECT COUNT(*) FROM runs WHERE state IN ('running','stopping')").fetchone()[0]
            if campaign_state != ("stopped",) or active:
                raise RuntimeError("stop and quiesce the campaign before adopting capacity policy")
            path = state / "pipeline.json"
            pipeline = json.loads(path.read_text())
            if pipeline.get("policy") == "capacity":
                return {"policy": "capacity", "changed": False}
            if pipeline.get("policy", "legacy") != "legacy":
                raise ValueError("unsupported prior campaign policy")
            backup = state / "pipeline.before-capacity.json"
            if backup.exists():
                raise ValueError("capacity backup already exists; inspect prior migration before retrying")
            prior = json.loads(path.read_text())
            pipeline["policy"] = "capacity"
            matching_policy(pipeline)
            validate_settings(pipeline["settings"], "capacity")
            atomic_save(backup, prior)
            atomic_save(path, pipeline)
            return {"policy": "capacity", "changed": True, "backup": str(backup),
                    "limits_unchanged": True, "campaign_state": "stopped"}


def main(policy_name="legacy") -> int:
    description = (__doc__ if policy_name == "legacy" else
                   "Continuous capacity policy: existing single-host matching, minimum-owner partitioned fallback.")
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--state", type=Path, required=True,
                        help="fresh launch_dp.py deployment directory")
    commands = parser.add_subparsers(dest="action")
    setup = commands.add_parser("init", help="create retained feeder policy")
    for key, default in DEFAULTS.items():
        setup.add_argument("--" + key.replace("_", "-"), type=int, default=default)
    if policy_name == "capacity":
        from campaigns.capacity_policy import DEFAULTS as extra
        for key, default in extra.items():
            setup.add_argument("--" + key.replace("_", "-"), type=int, default=default)
        commands.add_parser("plan", help="read-only capacity preview using current saved fields and live nodes")
        commands.add_parser("adopt", help="switch a stopped, upgraded deployment while retaining results and limits")
    commands.add_parser("once", help="perform one reconciliation pass")
    watch = commands.add_parser("run", help="reconcile indefinitely")
    watch.add_argument("--interval", type=int, default=120)
    commands.add_parser("status", help="print retained pipeline status")
    arguments = parser.parse_args()
    if arguments.action is None:
        parser.print_help()
        return 0
    state = arguments.state.resolve()
    try:
        if arguments.action == "adopt":
            print(json.dumps(adopt_capacity(state), indent=2))
        elif arguments.action == "plan":
            from campaigns.capacity_policy import CapacityPolicy, DEFAULTS as extra
            pipeline = json.loads((state / "pipeline.json").read_text())
            settings = {**DEFAULTS, **extra, **pipeline["settings"]}
            matching_policy({"policy": "capacity", "settings": settings})
            manifest = json.loads((state / "manifest.json").read_text())
            _, nodes = run_rows(state, manifest["leader"])
            plans = {field: CapacityPolicy().plan(load_dp(Path(record["dp_artifact"]))[0], settings, nodes)
                     for field, record in pipeline["fields"].items()
                     if record.get("dp_artifact") and not record.get("matching_complete")}
            print(json.dumps(plans, indent=2))
        elif arguments.action == "init":
            print(json.dumps(initialize(state, arguments, policy_name), indent=2))
        elif arguments.action == "once":
            if policy_name == "capacity" and json.loads((state / "pipeline.json").read_text()).get("policy") != "capacity":
                raise ValueError("not a capacity campaign; preview with plan, do not change the live feeder")
            print(json.dumps(reconcile(state), indent=2))
        elif arguments.action == "status":
            print((state / "PIPELINE_STATUS.md").read_text(), end="")
        else:
            if policy_name == "capacity" and json.loads((state / "pipeline.json").read_text()).get("policy") != "capacity":
                raise ValueError("not a capacity campaign; preview with plan, do not change the live feeder")
            if arguments.interval < 10:
                raise ValueError("poll interval must be at least 10 seconds")
            while True:
                try:
                    print(json.dumps(reconcile(state)), flush=True)
                except Exception as error:
                    print(f"continuous campaign will retry: {error}", file=sys.stderr, flush=True)
                time.sleep(arguments.interval)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"continuous_campaign.py: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
