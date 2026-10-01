"""Feeder panel: the retained pipeline policy, its limits, and what it does next."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from dp_solver import scheduling
from logs import LogWatcher, process_info

STALE_RECONCILE_SECONDS = 600


def feeder_process(state: Path) -> dict:
    """Report the feeder recorded in feeder_process.json, checked against /proc."""
    path = state / "feeder_process.json"
    if not path.exists():
        return {"recorded": False}
    record = json.loads(path.read_text())
    command = record.get("command") or []
    interval = None
    if "--interval" in command:
        try:
            interval = int(command[command.index("--interval") + 1])
        except (IndexError, ValueError):
            pass
    live = process_info(int(record["pid"]), "--state", str(state.name))
    return {"recorded": True, "pid": record["pid"], "alive": live is not None,
            "started": live and live["started"], "interval": interval or 120,
            "command": " ".join(command)}


def known_fields(state: Path) -> set[tuple[int, int]]:
    """Fields the feeder or launcher has already submitted."""
    known = set()
    try:
        pipeline = json.loads((state / "pipeline.json").read_text())
        known |= {(record["p"], record["r"]) for record in pipeline.get("fields", {}).values()}
    except (OSError, ValueError, KeyError):
        pass
    try:
        manifest = json.loads((state / "manifest.json").read_text())
        known |= {(entry["specification"]["arguments"]["p"], entry["specification"]["arguments"]["r"])
                  for entry in manifest.get("entries", [])
                  if "p" in entry["specification"].get("arguments", {})}
    except (OSError, ValueError, KeyError):
        pass
    return known


def upcoming_fields(state: Path, settings: dict, limit: int = 8) -> list[dict]:
    """The next unsubmitted fields the feeder would add under these settings, in its order."""
    known = known_fields(state)
    upcoming = []
    for candidate in scheduling.campaign(settings.get("max_state_bytes", 16 * 1024**3),
                                         settings.get("max_visits", 30_000_000_000_000),
                                         settings.get("dp_threads", 16), settings.get("tile_side", 512)):
        arguments = candidate["arguments"]
        key = (arguments["p"], arguments["r"])
        if key not in known:
            upcoming.append({"field": list(key), "q": key[0] ** key[1]})
            if len(upcoming) >= limit:
                break
    return upcoming


def build_feeder(state: Path, watcher: LogWatcher, roots: list[dict] | None, now: float) -> dict | None:
    path = state / "pipeline.json"
    if not path.exists():
        return None
    pipeline = json.loads(path.read_text())
    settings = pipeline.get("settings", {})
    fields = pipeline.get("fields", {})
    process = feeder_process(state)
    max_attempts = settings.get("max_dp_attempts", 3)

    root_progress = {}
    for root in roots or []:
        total = len(root["cells"])
        done = root["counts"]["durable"] + root["counts"]["complete"]
        root_progress[root["run_id"]] = {"done": done, "total": total, "state": root["state"]}

    in_flight, complete = [], 0
    for name, record in fields.items():
        if record.get("matching_complete"):
            complete += 1
            continue
        attempts = record.get("dp_attempts", [])
        failed = sum(item.get("state") == "failed" for item in attempts)
        matching = record.get("matching_attempts", [])
        latest = matching[-1] if matching else None
        plan = record.get("matching_plan") or {}
        current = record.get("dp_current_run_id")
        given_up = (not record.get("dp_artifact") and failed >= max_attempts and
                    record.get("dp_current_state") in {"failed", "cancelled", None})
        in_flight.append({
            "field": [record["p"], record["r"]], "q": record.get("q"),
            "dp_state": record.get("dp_state"), "dp_attempts": len(attempts),
            "dp_failed": failed, "dp_given_up": given_up,
            "dp_progress": root_progress.get(current),
            "requests": record.get("requests"), "edges": record.get("edges"),
            "matching_state": latest.get("state") if latest else None,
            "matching_attempts": len(matching),
            "poly": latest.get("poly") if latest else None,
            "admission": record.get("matching_admission"),
            "engine": f"{plan['program']} / {plan['workers']}" if plan.get("program") else None,
            "notes": [text for text in (
                record.get("matching_failure") and f"matching gave up: {record['matching_failure']}",
                record.get("candidate_exhausted") and "every primitive polynomial tried") if text],
        })
    in_flight.sort(key=lambda item: (item["q"] or 0, item["field"]))

    matchable = sum(1 for record in fields.values()
                    if record.get("dp_artifact") and not record.get("matching_complete") and
                    record.get("matching_admission") in {"admitted", "waiting for nodes"})
    demand = pipeline.get("demand", {})
    free = shutil.disk_usage(state).free

    upcoming = upcoming_fields(state, settings)

    history = [{"time": event["time"], "after": event.get("after"), "kind": event["kind"],
                "result": event.get("result"), "message": event.get("message")}
               for event in list(watcher.baseline)[-60:] + list(watcher.observed)[-200:]][-80:]

    return {
        "state": pipeline.get("feeder_state"), "policy": pipeline.get("policy", "legacy"),
        "last_reconcile": pipeline.get("last_reconcile"),
        "stale": (pipeline.get("last_reconcile") is None or
                  now - pipeline["last_reconcile"] > STALE_RECONCILE_SECONDS),
        "process": process,
        "demand": demand,
        "gauges": [
            {"label": "Ready DP tiles", "value": demand.get("ready_dp_tiles"),
             "target": demand.get("ready_tile_target"),
             "note": "the feeder adds roots while this is below target"},
            {"label": "Active DP roots", "value": demand.get("active_dp_roots"),
             "target": settings.get("max_dp_roots"),
             "note": f"target {settings.get('target_dp_roots')}, hard cap {settings.get('max_dp_roots')}"},
            {"label": "Matchable backlog", "value": matchable, "target": settings.get("max_ready_fields"),
             "note": "DP expansion pauses at the limit (matching backpressure)", "limit": True},
            {"label": "Free disk (leader)", "value": free, "target": settings.get("minimum_free_bytes"),
             "note": "DP expansion pauses below the watermark", "bytes": True, "floor": True},
        ],
        "settings": settings,
        "fields_complete": complete,
        "in_flight": in_flight,
        "upcoming": upcoming,
        "history": history,
        "observing_since": watcher.started,
    }
