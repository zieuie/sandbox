"""Problems feed: conditions that need attention now, and recent failures.

Every item has a severity (critical, warning, info) and a group key; the page
collapses items sharing a key, so a burst of tile failures reads as one line.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

from logs import LogWatcher

EVENT_WINDOW = 7 * 24 * 3600
RECENT_LOG_WINDOW = 3600


def label(field) -> str:
    if not field:
        return "?"
    superscript = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    return f"{field[0]}{str(field[1]).translate(superscript)}"


def item(severity, group, title, detail=None, time_=None, link=None, **extra) -> dict:
    return {"severity": severity, "group": group, "title": title, "detail": detail,
            "time": time_, "link": link, **extra}


def current_conditions(snapshot: dict, connection: sqlite3.Connection, now: float,
                       leader_log: LogWatcher, feeder_log: LogWatcher) -> list[dict]:
    found = []
    status = snapshot.get("status") or {}
    fleet = snapshot.get("fleet") or {}
    feeder = snapshot.get("feeder")

    for node in fleet.get("nodes", []):
        if node["state"] == "unavailable":
            found.append(item("critical", f"node_down:{node['name']}",
                              f"{node['hostname']} is not sending heartbeats",
                              f"last heartbeat {int(node['heartbeat_age'])} s ago",
                              now - node["heartbeat_age"], "#fleet"))
        for work in node["work"]:
            health = work["health"]
            if health in {"stalled", "heartbeat-missing"}:
                severity = "critical"
            elif health == "no-progress-warning":
                severity = "warning"
            else:
                severity = None
            if severity:
                found.append(item(severity, f"solver:{work['run_id']}",
                                  f"{label(work['field'])} {work['label']} on {node['hostname']}: {health}",
                                  f"phase {work['phase']}", work["started"], "#fleet"))
    if status and status.get("dispatch") != "running":
        found.append(item("warning", "dispatch", f"Dispatch is {status.get('dispatch')}",
                          "No new leases are handed out until it is resumed.", None, "#fleet",
                          action={"command": "dispatch.resume", "params": {}, "label": "Resume dispatch"}))

    # The scheduler refuses to clear a large share of a root's finished tiles at once (see
    # dp_solver.distributed.recompute_lost_tiles); while it holds, nothing there is recomputed.
    holds = {}
    for held in connection.execute("SELECT key,value FROM settings WHERE key LIKE 'tile_clear_hold:%'"):
        try:
            holds[held["key"].split(":", 1)[1]] = json.loads(held["value"])
        except ValueError:
            pass
    for root in snapshot.get("roots") or []:
        hold = holds.get(root["run_id"])
        if hold and root["state"] not in {"complete", "failed", "cancelled"}:
            found.append(item("critical", f"clear_hold:{root['p']},{root['r']}",
                              f"{label([root['p'], root['r']])}: {hold['would_clear']} of {hold['finished']} finished "
                              "tiles look lost, so none are being recomputed",
                              "Their copies are missing from the leader's records, but a fleet-wide worker restart "
                              "does that too. Check that the workers have finished revalidating; if the copies "
                              "really are gone, raise the tile_clear_max_fraction setting to let them recompute.",
                              hold["time"], "#tiles"))
        stuck = [cell for cell in root["cells"] if cell["s"] == "cancelled"]
        if root["state"] not in {"complete", "failed", "cancelled"} and stuck:
            found.append(item("warning", f"stuck:{root['p']},{root['r']}",
                              f"{label([root['p'], root['r']])} cannot finish: tile "
                              f"{stuck[0]['r']},{stuck[0]['c']} was cancelled",
                              "The leader never recreates a cancelled tile. Restart the field to recompute it; "
                              "finished tiles are reused.", None, "#tiles",
                              action={"command": "root.restart", "params": {"run_id": root["run_id"]},
                                      "label": "Restart field"}))
        # Every tile is done, but the reconstruction lease (which needs a whole
        # machine) has not been granted: it queues behind other fields' tiles.
        waited = now - (root.get("last_progress_at") or now)
        if root["state"] == "queued" and root.get("phase") == "reconstructing" and waited > 600:
            found.append(item("warning", f"reconstruct:{root['p']},{root['r']}",
                              f"{label([root['p'], root['r']])} is waiting to reconstruct its result",
                              f"All tiles finished {int(waited // 60)} minutes ago, but the final step has not "
                              "started. It needs a whole machine and is queued behind other fields' tiles; "
                              f"raising its priority above {root.get('priority', 0)} lets the next free machine "
                              "take it.", root.get("last_progress_at"), "#tiles",
                              action={"command": "run.priority", "params": {"run_id": root["run_id"]},
                                      "label": "Raise priority…"}))
        if root.get("orphaned_children"):
            found.append(item("warning", f"orphaned:{root['p']},{root['r']}",
                              f"{label([root['p'], root['r']])}: {root['orphaned_children']} tiles still "
                              f"running after the root {root['state']}",
                              "These leases occupy machines for a calculation that has stopped.",
                              root["finished"], "#tiles",
                              action={"command": "root.cancel_leftovers", "params": {"run_id": root["run_id"]},
                                      "label": "Cancel leftover tiles"}))

    if feeder:
        process = feeder["process"]
        if process.get("recorded") and not process.get("alive"):
            found.append(item("critical", "feeder_down", "The feeder process is not running",
                              f"pid {process['pid']} from feeder_process.json is gone. "
                              "No DP is collected, matched or added.", None, "#feeder",
                              action={"command": "process.ensure_feeder", "params": {},
                                      "label": "Start the feeder"}))
        elif feeder["stale"]:
            found.append(item("critical", "feeder_stale", "The feeder has not reconciled recently",
                              "Its last successful pass is more than 10 minutes old.",
                              feeder["last_reconcile"], "#feeder"))
        latest = (list(feeder_log.baseline) + list(feeder_log.observed))[-1:]
        if latest and latest[0]["kind"] == "feeder_error":
            found.append(item("warning", "feeder_failing", "The feeder's latest pass failed",
                              latest[0]["message"], latest[0]["time"], "#feeder"))
        for gauge in feeder["gauges"]:
            if gauge.get("floor") and gauge["value"] is not None and gauge["target"] and \
                    gauge["value"] < 2 * gauge["target"]:
                below = gauge["value"] < gauge["target"]
                found.append(item("critical" if below else "warning", "disk",
                                  "Leader disk below the feeder's watermark" if below
                                  else "Leader disk approaching the feeder's watermark",
                                  f"{gauge['value'] / 1024**3:.1f} GiB free; watermark "
                                  f"{gauge['target'] / 1024**3:.0f} GiB", None, "#feeder"))
        for record in feeder["in_flight"]:
            if record["dp_given_up"]:
                found.append(item("critical", f"gave_up:{record['field'][0]},{record['field'][1]}",
                                  f"{label(record['field'])}: DP failed all {record['dp_attempts']} attempts",
                                  "The feeder will not retry this field on its own.", None,
                                  f"#results/{record['field'][0]},{record['field'][1]}",
                                  action={"command": "feeder.retry",
                                          "params": {"p": record["field"][0], "r": record["field"][1]},
                                          "label": "Retry"}))
            for note in record["notes"]:
                found.append(item("warning", f"matching_gave_up:{record['field'][0]},{record['field'][1]}",
                                  f"{label(record['field'])}: {note}", None, None,
                                  f"#results/{record['field'][0]},{record['field'][1]}"))

    lease_seconds = float(status.get("lease_seconds") or 60)
    for row in connection.execute(
            "WITH roots AS (SELECT DISTINCT artifact_hash FROM runs WHERE parent_run_id IS NULL "
            "AND artifact_hash IS NOT NULL AND state='complete') "
            "SELECT a.artifact_hash,a.target_replicas,COUNT(n.node_name) AS live FROM roots "
            "JOIN artifacts a USING(artifact_hash) LEFT JOIN replicas r USING(artifact_hash) "
            "LEFT JOIN nodes n ON n.node_name=r.node_name AND n.last_heartbeat>? "
            "GROUP BY a.artifact_hash HAVING live<a.target_replicas", (now - lease_seconds,)):
        found.append(item("warning", "replicas", "A completed result has too few live copies",
                          f"artifact {row['artifact_hash'][:12]}: {row['live']} of {row['target_replicas']}",
                          None, "#fleet"))

    # Repeated leader errors in the last hour, from what the dashboard has seen.
    recent: dict[str, list[dict]] = {}
    for event in leader_log.observed:
        if event["kind"] == "leader_exception" and not event["benign"] and event["time"] >= now - RECENT_LOG_WINDOW:
            recent.setdefault(f"{event['type']}: {event['message']}", []).append(event)
    for text, events in recent.items():
        found.append(item("warning", f"leader_recent:{text}",
                          f"Leader logged “{text}” {len(events)}× in the last hour",
                          "Seen by the dashboard while watching leader.log.", events[-1]["time"], None,
                          count=len(events)))
    return found


def recent_events(snapshot: dict, connection: sqlite3.Connection, now: float,
                  describe: Callable[[dict], dict], max_attempts: int,
                  leader_log: LogWatcher, feeder_log: LogWatcher) -> list[dict]:
    start = now - EVENT_WINDOW
    events = []
    nodes = (snapshot.get("fleet") or {}).get("nodes", [])
    names = {node["name"]: node["hostname"] for node in nodes}
    by_address = {node["host"]: node["hostname"] for node in nodes}

    attempt_index: dict[str, tuple[int, int]] = {}
    by_field: dict[tuple, list[str]] = {}
    for row in connection.execute(
            "SELECT run_id,specification FROM runs WHERE parent_run_id IS NULL "
            "AND specification LIKE '%dp_distributed%' ORDER BY created"):
        arguments = json.loads(row["specification"]).get("arguments", {})
        by_field.setdefault((arguments.get("p"), arguments.get("r")), []).append(row["run_id"])
    for runs in by_field.values():
        for index, run_id in enumerate(runs):
            attempt_index[run_id] = (index + 1, len(runs))

    for row in connection.execute(
            "SELECT run_id,calculation_id,specification,state,node_name,finished,error,parent_run_id "
            "FROM runs WHERE state='failed' AND finished>?", (start,)):
        run = dict(row)
        description = describe(run)
        field = description.get("field")
        key = f"{field[0]},{field[1]}" if field else "?"
        node = names.get(run["node_name"], run["node_name"])
        if description["kind"] == "dp_distributed":
            attempt, _ = attempt_index.get(run["run_id"], (1, 1))
            last = attempt >= max_attempts
            events.append(item("critical" if last else "warning", f"dp_root:{key}",
                               f"{label(field)} DP attempt {attempt} of {max_attempts} failed"
                               + (" — no retries left" if last else ""),
                               run["error"], run["finished"], f"#results/{key}"))
        elif description["kind"] == "dp_tile":
            events.append(item("warning", f"tile_failed:{key}",
                               f"{label(field)} {description['label']} failed on {node}",
                               run["error"], run["finished"], "#tiles"))
        elif description["kind"] == "matching":
            events.append(item("warning", f"matching_failed:{key}",
                               f"{label(field)} matching failed", run["error"], run["finished"],
                               f"#results/{key}"))
        else:
            events.append(item("warning", f"run_failed:{description['kind']}",
                               f"{description['label']} failed", run["error"], run["finished"]))

    for row in connection.execute(
            "SELECT h.outcome,h.node_name,h.finished,r.run_id,r.calculation_id,r.specification "
            "FROM lease_history h JOIN runs r USING(run_id) "
            "WHERE h.outcome IN ('engine retry','lease expired') AND h.finished>?", (start,)):
        run = dict(row)
        description = describe(run)
        field = description.get("field")
        node = names.get(run["node_name"], run["node_name"])
        what = f"{label(field)} {description['label']}" if field else description["label"]
        key = f"{field[0]},{field[1]}" if field else "?"
        if run["outcome"] == "engine retry":
            events.append(item("info", f"engine_retry:{key}",
                               f"{what} retried after an engine failure on {node}",
                               "The leader requeued it on another machine.", run["finished"], "#tiles"))
        else:
            events.append(item("warning", f"lease_expired:{run['node_name']}",
                               f"Lease for {what} on {node} expired",
                               "The agent stopped renewing its lease; the work was requeued.",
                               run["finished"], "#fleet"))

    def log_time(event):
        return event["time"], event.get("after")

    for event in list(leader_log.baseline) + list(leader_log.observed):
        when, after = log_time(event)
        if event["kind"] == "leader_exception":
            text = f"{event['type']}: {event['message']}".rstrip(": ")
            client = event.get("client")
            client_name = by_address.get(client, client)
            events.append(item("info" if event["benign"] else "warning", f"leader_log:{text}",
                               f"Leader error: {text}",
                               f"while serving {client_name}" if client else None,
                               when, None, after=after, from_log=True))
        elif event["kind"] == "leader_start":
            events.append(item("info", "leader_start", "Leader started", event["message"], when, None,
                               after=after, from_log=True))
        elif event["kind"] == "scheduler_retry":
            events.append(item("warning", "scheduler_retry", "Leader scheduler transaction failed",
                               "It retries automatically.", when, None, after=after, from_log=True))
    for event in list(feeder_log.baseline) + list(feeder_log.observed):
        if event["kind"] == "feeder_error":
            when, after = log_time(event)
            events.append(item("warning", f"feeder_error:{event['message']}",
                               f"Feeder pass failed: {event['message']}", "The feeder retries next pass.",
                               when, "#feeder", after=after, from_log=True))
    return events


def build_problems(snapshot: dict, connection: sqlite3.Connection, now: float,
                   describe: Callable[[dict], dict], leader_log: LogWatcher,
                   feeder_log: LogWatcher) -> dict:
    settings = {}
    if snapshot.get("feeder"):
        settings = snapshot["feeder"]["settings"]
    active = current_conditions(snapshot, connection, now, leader_log, feeder_log)
    events = recent_events(snapshot, connection, now, describe, settings.get("max_dp_attempts", 3),
                           leader_log, feeder_log)
    rank = {"critical": 0, "warning": 1, "info": 2}
    active.sort(key=lambda entry: (rank[entry["severity"]], -(entry["time"] or 0)))
    return {
        "active": active,
        "events": events,
        "window_seconds": EVENT_WINDOW,
        "log_observed_since": min(filter(None, (leader_log.started, feeder_log.started)), default=None),
        "counts": {severity: sum(entry["severity"] == severity for entry in active)
                   for severity in rank},
    }
