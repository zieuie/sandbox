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

TERMINAL = {"complete", "failed", "cancelled"}
DEFAULTS = {
    "target_dp_roots": 2, "max_ready_fields": 4, "max_dp_attempts": 3,
    "max_dp_roots": 8, "target_ready_dp_tiles": 18,
    "max_matching_attempts": 3, "matching_retry_seconds": 300,
    "max_state_bytes": 16 * 1024**3, "max_visits": 30_000_000_000_000,
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


def run_rows(state: Path, leader: str) -> tuple[dict[str, dict], list[dict]]:
    """Return all roots plus nodes, bypassing the public status row limit."""
    status = request(leader, "/v1/status")
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


def matching_worker_count(dp: dict, settings: dict) -> int:
    """Choose a configured small, medium, or full group by retained request count."""
    count = request_count(dp)
    if count <= settings["matching_small_requests"]:
        return min(settings["matching_workers"], settings["matching_small_workers"])
    if count <= settings["matching_medium_requests"]:
        return min(settings["matching_workers"], settings["matching_medium_workers"])
    return settings["matching_workers"]


def matchable_backlog(pipeline: dict) -> int:
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
        admitted, reason = matching_admitted(dp, settings)
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
                    polynomial: list[int], rerun: bool = False) -> None:
    settings = pipeline["settings"]
    dp, _ = load_dp(Path(record["dp_artifact"]))
    workers = matching_worker_count(dp, settings)
    job = matching_specification(
        Path(record["dp_artifact"]), ",".join(map(str, polynomial)),
        settings["matching_threads"], settings["max_matching_bytes"],
        distributed=True, max_edges=settings["max_matching_edges"],
        max_field_elements=settings["max_field_elements"],
        workers=workers,
    )
    payload = {"specification": job,
               "priority": settings["matching_priority"]}
    if rerun:
        payload["rerun"] = True
    result = request(manifest["leader"], "/v1/enqueue", payload)
    record["matching_attempts"].append({"poly": polynomial, **result})


def advance_matching(state: Path, manifest: dict, runs: dict[str, dict],
                     nodes: list[dict], pipeline: dict) -> dict[str, int]:
    """Archive outcomes, retry obstructions, and submit ready fields."""
    counts, settings = Counter(), pipeline["settings"]
    live_nodes = sum(bool(node.get("compute_enabled", True)) and
                     node.get("state", "healthy") == "healthy" for node in nodes)
    records = sorted(pipeline["fields"].values(), key=lambda item: item.get("edges", 2**63))
    for record in records:
        if not record.get("dp_artifact") or record.get("matching_complete"):
            continue
        dp, _ = load_dp(Path(record["dp_artifact"]))
        admitted, reason = matching_admitted(dp, settings)
        record["matching_admission"] = reason
        if not admitted:
            counts["inadmissible"] += 1
            continue
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
            if run["state"] in {"failed", "cancelled"}:
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
                enqueue_attempt(manifest, pipeline, record, latest["poly"], rerun=True)
                counts["retried"] += 1
                continue
            if run["state"] != "complete":
                continue
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
        else:
            polynomial = first_primitive(record["p"], record["r"], record["q"])
        required_workers = matching_worker_count(dp, settings)
        if live_nodes < required_workers:
            record["matching_admission"] = "waiting for nodes"
            counts["waiting_nodes"] += 1
            continue
        enqueue_attempt(manifest, pipeline, record, polynomial)
        counts["submitted"] += 1
    return dict(counts)


def replenish_dp(state: Path, manifest: dict, runs: dict[str, dict], pipeline: dict,
                 nodes: list[dict] | tuple = ()) -> int:
    """Keep a bounded root backlog while respecting disk and matchable pressure."""
    settings = pipeline["settings"]
    if shutil.disk_usage(state).free < settings["minimum_free_bytes"]:
        pipeline["feeder_state"] = "local disk watermark"
        return 0
    ready = matchable_backlog(pipeline)
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
    for candidate in scheduling.campaign(settings["max_state_bytes"], settings["max_visits"],
                                         settings["dp_threads"], settings["tile_side"]):
        if added >= needed:
            break
        arguments = candidate["arguments"]
        field = (arguments["p"], arguments["r"])
        if field in known:
            continue
        candidate["program"] = "dp_distributed"
        arguments.update(max_tile_bytes=settings["max_tile_bytes"], artifact_format="KHD1")
        result = request(manifest["leader"], "/v1/enqueue", {"specification": candidate})
        manifest["entries"].append({"specification": candidate, **result})
        save(state / "manifest.json", manifest)
        known.add(field)
        added += 1
        if added >= needed:
            break
    pipeline["feeder_state"] = "running" if added or active else "frontier exhausted"
    return added


def render_status(pipeline: dict, runs: dict[str, dict], nodes: list[dict]) -> str:
    lines = ["# Continuous DP and matching campaign", "",
             f"Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
             f"Feeder: `{pipeline.get('feeder_state', 'unknown')}`; nodes: {len(nodes)}", "",
             ("Demand: " + ", ".join(f"{key}={value}" for key, value in
                                      pipeline.get("demand", {}).items())) if pipeline.get("demand") else "",
             "" if pipeline.get("demand") else "",
             "| Field | DP | Matching | Attempts | Admission |",
             "| --- | --- | --- | ---: | --- |"]
    for field, record in sorted(pipeline["fields"].items(), key=lambda item: item[1].get("edges", 2**63)):
        attempts = record.get("matching_attempts", [])
        matching = "complete" if record.get("matching_complete") else "not queued"
        if attempts:
            selected = preferred_attempt(attempts, runs)
            attempt, run = selected if selected is not None else (attempts[-1], None)
            matching = (run or attempt).get("state", "unknown")
        lines.append(f"| {field} | {record.get('dp_state', 'unknown')} | {matching} | {len(attempts)} | {record.get('matching_admission', '—')} |")
    return "\n".join(lines) + "\n"


def reconcile(state: Path) -> dict:
    """Perform one restart-safe collect, match, and refill transaction."""
    pipeline_path = state / "pipeline.json"
    with (state / "pipeline.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pipeline = json.loads(pipeline_path.read_text())
        for key, value in DEFAULTS.items():
            pipeline.setdefault("settings", {}).setdefault(key, value)
        validate_settings(pipeline["settings"])
        manifest = json.loads((state / "manifest.json").read_text())
        runs, nodes = run_rows(state, manifest["leader"])
        collected = collect_dp(state, manifest, runs, pipeline, nodes)
        matching = advance_matching(state, manifest, runs, nodes, pipeline)
        added = replenish_dp(state, manifest, runs, pipeline, nodes)
        pipeline["last_reconcile"] = time.time()
        atomic_save(pipeline_path, pipeline)
        temporary = state / "PIPELINE_STATUS.tmp"
        temporary.write_text(render_status(pipeline, runs, nodes))
        temporary.replace(state / "PIPELINE_STATUS.md")
        return {"collected_dp": collected, "matching": matching, "added_dp": added,
                "fields": len(pipeline["fields"]), "nodes": len(nodes),
                "feeder": pipeline["feeder_state"]}


def validate_settings(settings: dict) -> None:
    positive = ("target_dp_roots", "max_dp_roots", "target_ready_dp_tiles",
                "max_ready_fields", "max_dp_attempts",
                "max_matching_attempts", "matching_retry_seconds",
                "max_state_bytes", "max_visits",
                "dp_threads", "tile_side", "max_tile_bytes", "matching_workers",
                "matching_small_workers", "matching_medium_workers",
                "matching_small_requests", "matching_medium_requests",
                "matching_threads", "max_matching_bytes", "max_matching_edges",
                "max_field_elements", "minimum_free_bytes")
    if any(type(settings.get(key)) is not int or settings[key] <= 0 for key in positive):
        raise ValueError("pipeline limits must be positive integers")
    if settings["target_dp_roots"] > settings["max_dp_roots"]:
        raise ValueError("target DP roots exceed the hard root cap")
    if not 2 <= settings["matching_workers"] <= 256:
        raise ValueError("distributed matching requires 2-256 workers")
    if not 2 <= settings["matching_small_workers"] <= 256 or \
            not 2 <= settings["matching_medium_workers"] <= 256 or \
            settings["matching_small_requests"] > settings["matching_medium_requests"]:
        raise ValueError("invalid matching group tiers")
    if settings["matching_workers"] * settings["matching_threads"] > 256:
        raise ValueError("matching worker/thread product exceeds adapter limit")


def initialize(state: Path, arguments: argparse.Namespace) -> dict:
    """Create pipeline policy beside an existing fresh unified deployment."""
    if not (state / "manifest.json").exists():
        raise ValueError("start the unified cluster deployment before initializing its feeder")
    path = state / "pipeline.json"
    if path.exists():
        raise ValueError("pipeline state already exists")
    settings = {key: getattr(arguments, key) for key in DEFAULTS}
    validate_settings(settings)
    pipeline = {"version": 1, "settings": settings, "fields": {},
                "feeder_state": "initialized", "created": time.time()}
    atomic_save(path, pipeline)
    return pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True,
                        help="fresh launch_dp.py deployment directory")
    commands = parser.add_subparsers(dest="action")
    setup = commands.add_parser("init", help="create retained feeder policy")
    for key, default in DEFAULTS.items():
        setup.add_argument("--" + key.replace("_", "-"), type=int, default=default)
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
        if arguments.action == "init":
            print(json.dumps(initialize(state, arguments), indent=2))
        elif arguments.action == "once":
            print(json.dumps(reconcile(state), indent=2))
        elif arguments.action == "status":
            print((state / "PIPELINE_STATUS.md").read_text(), end="")
        else:
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
