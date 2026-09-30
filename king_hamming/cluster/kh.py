#!/usr/bin/env python3
"""Control a first-draft king_hamming leader."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from common import canonical_json
import adapters


# Send one JSON request to the leader.
def request_json(leader: str, method: str, path: str, value: Any = None) -> Any:
    """Return the decoded response from a leader endpoint."""

    body = None if value is None else canonical_json(value)
    request = Request(f"{leader.rstrip('/')}{path}", data=body, method=method)

    if body is not None:
        request.add_header("Content-Type", "application/json")

    with urlopen(request, timeout=15) as response:
        return json.load(response)


# Construct the operator command interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the kh command-line parser."""

    parser = argparse.ArgumentParser(
        description="Operate the first-draft king_hamming queue.",
        epilog="Example: ./kh.py --leader http://merlin:8041 enqueue ../dp_solver/dp_5_3.json",
    )
    parser.add_argument("--leader", default="http://127.0.0.1:8041")
    commands = parser.add_subparsers(dest="command")
    enqueue = commands.add_parser("enqueue", help="enqueue a JSON calculation specification")
    enqueue.add_argument("specification", type=Path)
    enqueue.add_argument("--priority", type=int, default=0)
    mode = enqueue.add_mutually_exclusive_group()
    mode.add_argument("--rerun", action="store_true", help="create another retained attempt")
    mode.add_argument("--from-scratch", action="store_true", help="recompute every requested stage")
    campaign = commands.add_parser("campaign", help="preview or enqueue the configured solver campaign")
    adapters.default_campaign().configure_campaign(campaign)
    status = commands.add_parser("status", help="show nodes and recent runs")
    status.add_argument("--watch", type=float, metavar="SECONDS", help="refresh periodically")
    status.add_argument("--verbose", action="store_true",
                        help="include artifacts, completed history, checkpoints, and resources")
    stop = commands.add_parser("stop", help="stop dispatch without recovery escalation")
    stop.add_argument("--all", action="store_true", help="explicitly select the whole campaign")
    resume = commands.add_parser("resume", help="resume dispatch")
    resume.add_argument("--all", action="store_true", help="explicitly select the whole campaign")
    cancel = commands.add_parser("cancel", help="cancel one queued, paused, or running run")
    cancel.add_argument("run_id")
    pause = commands.add_parser("pause-run", help="pause one queued or running run")
    pause.add_argument("run_id")
    resume_run = commands.add_parser("resume-run", help="resume one paused run")
    resume_run.add_argument("run_id")
    priority = commands.add_parser("reprioritize", help="change one run's queue priority")
    priority.add_argument("run_id")
    priority.add_argument("priority", type=int)
    return parser


def print_status(status: dict[str, Any], verbose: bool = False) -> None:
    """Print concise current status, with retained history only when verbose."""

    scheduler = status.get("scheduler", {})
    scheduler_text = f" scheduler={scheduler.get('status', 'unknown')}"
    print(f"campaign: {status['campaign_state']} schema={status.get('schema_version', 'legacy')} "
          f"checkpoint={status['checkpoint_seconds']}s "
          f"lease={status.get('lease_seconds', 'unknown')}s keep={status.get('checkpoint_keep', 'unknown')}"
          f"{scheduler_text}")
    if scheduler.get("last_error"):
        print(f"scheduler_error: {scheduler['last_error']}")
    descriptions = {}
    for run in status["runs"]:
        try:
            specification = json.loads(run["specification"])
            descriptions[run["run_id"]] = adapters.get(specification).describe(specification)
        except (KeyError, ValueError, TypeError):
            descriptions[run["run_id"]] = "unknown calculation"
    active = {}
    for run in status["runs"]:
        if run["state"] == "running" and run["node_name"]:
            active.setdefault(run["node_name"], []).append(run)
    print("nodes:")

    for node in status["nodes"]:
        role = "compute+storage" if node.get("compute_enabled", 1) else "storage"
        current = active.get(node["node_name"], [])
        reserved = node.get("reserved_for")
        calculations = ",".join(descriptions.get(run["run_id"], "unknown")
                                for run in current)
        work = (f" calculating={calculations}"
                if current else f" reserved_for={descriptions.get(reserved, reserved)}"
                if reserved else f" idle={node.get('idle_reason', 'unknown')}")
        version = (f" version={node['runtime_version'][:12]}"
                   if verbose and node.get("runtime_version") else "")
        topology = (f" physical_cores={node['physical_core_count']}"
                    if verbose and node.get("physical_core_count") else "")
        slots = (f" slots={len(json.loads(node.get('slots_json') or '[]'))}"
                 if verbose else "")
        allocated = {cpu for run in current
                     for cpu in (run.get("assigned_cpu_set") or node["cpu_set"]).split(",")
                     if cpu}
        if reserved:
            allocated.update(cpu for cpu in node["cpu_set"].split(",") if cpu)
        capacity = len([cpu for cpu in node["cpu_set"].split(",") if cpu])
        print(f"  {node['node_name']}: {node['state']} role={role} cpus={node['cpu_set']}"
              f" allocated={len(allocated)}/{capacity}{topology}{slots}{version}{work}")

    if verbose:
        print("artifacts:")
        for artifact in status.get("artifacts", []):
            print(
                f"  {artifact['artifact_hash']} replicas="
                f"{artifact['replicas']}/{artifact['target_replicas']}"
            )

    runs = status["runs"] if verbose else [
        run for run in status["runs"]
        if run.get("parent_run_id") is None and
        run.get("state") not in {"complete", "failed", "cancelled"}
    ]
    print("runs:" if verbose else "in progress:")

    if not runs:
        print("  none")
    for run in runs:
        progress = f"{run['progress_done']}/{run['progress_total']} {run['progress_units']}"
        print(f"  {run['run_id']} {run['state']:8} {progress:>12} node={run['node_name'] or '-'} "
              f"calculation={descriptions[run['run_id']]}")

        specification = json.loads(run["specification"])
        try:
            details = adapters.get(specification).status_details(
                specification, run, status)
        except (KeyError, TypeError, ValueError):
            details = []
        if details:
            print("    " + "; ".join(details))

        if not verbose:
            continue

        heartbeat_at = run["last_solver_heartbeat"]
        heartbeat_age = "unknown" if heartbeat_at is None else f"{max(0, time.time() - heartbeat_at):.0f}s"
        checkpoint_at = run["last_checkpoint_at"]
        checkpoint_age = "unknown" if checkpoint_at is None else f"{max(0, time.time() - checkpoint_at):.0f}s"
        print(
            f"    health={run['solver_health']} phase={run['progress_phase']} "
            f"heartbeat_age={heartbeat_age} "
            f"durable={run['progress_checkpoint_done']} checkpoint_age={checkpoint_age}"
        )
        try:
            progress_details = json.loads(run.get("progress_details") or "{}")
        except (TypeError, ValueError):
            progress_details = {}
        if progress_details:
            detail = " ".join(f"{key}={value}" for key, value in progress_details.items())
            print(f"    activity {detail}")

        print(
            f"    replicated={run.get('replicated_checkpoint_done', 0)} "
            f"live_copies={run.get('checkpoint_replicas', 0)}/2 "
            f"restored={run.get('restored_done', 0)} attempt={run.get('lease_attempt', 0)} "
            f"recoveries={run.get('recovery_count', 0)} "
            f"checkpoints={run.get('retained_checkpoints', 0)} retained/{run.get('retired_checkpoints', 0)} retired "
            f"estimated_seconds={run.get('estimated_seconds', 0):.3g}"
        )

        for usage in run.get("resource_usage", []):
            cpu_seconds = usage["cpu_microseconds"] / 1_000_000
            peak_mib = usage["peak_rss_bytes"] / (1024 * 1024)
            component = (usage["component"] if usage["shard_index"] < 0 else
                         f"{usage['component']}[{usage['shard_index']}]")
            print(
                f"    resources attempt={usage.get('attempt') or '?'} node={usage['node_name']} "
                f"{component} cpu={cpu_seconds:.3f}s peak_rss={peak_mib:.1f}MiB"
                + (f" assigned_cpu={usage['assigned_cpu_utilization']:.0%}"
                   f"/{usage['sample_seconds']:.0f}s"
                   if "assigned_cpu_utilization" in usage else "")
            )

        if run["artifact_location"]:
            print(f"    artifact: {run['artifact_location']}")


# Execute one operator command.
def main() -> int:
    """Run the selected operator command and return an exit status."""

    parser = build_parser()
    arguments = parser.parse_args()

    if arguments.command is None:
        parser.print_help()
        return 0

    if arguments.command == "campaign":
        try:
            entries = adapters.default_campaign().campaign_entries(arguments)
            for entry in entries:
                if arguments.submit:
                    result = request_json(arguments.leader, "POST", "/v1/enqueue", {"specification": entry["specification"]})
                    entry = {**result, **entry}
                print(json.dumps(entry))
        except ValueError as error:
            parser.error(str(error))
        return 0

    if arguments.command == "enqueue":
        specification = json.loads(arguments.specification.read_text())
        result = request_json(
            arguments.leader,
            "POST",
            "/v1/enqueue",
            {
                "specification": specification,
                "priority": arguments.priority,
                "rerun": arguments.rerun,
                "from_scratch": arguments.from_scratch,
            },
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif arguments.command == "status":
        try:
            while True:
                print_status(request_json(arguments.leader, "GET", "/v1/status"),
                             verbose=arguments.verbose)

                if arguments.watch is None:
                    break

                print()
                time.sleep(arguments.watch)
        except KeyboardInterrupt:
            pass
    elif arguments.command in {"cancel", "pause-run", "resume-run", "reprioritize"}:
        action = {"pause-run": "pause", "resume-run": "resume"}.get(arguments.command, arguments.command)
        body = {"run_id": arguments.run_id, "action": action}
        if arguments.command == "reprioritize":
            body["priority"] = arguments.priority
        print(json.dumps(request_json(arguments.leader, "POST", "/v1/run-command", body), indent=2, sort_keys=True))
    else:
        state = "stopped" if arguments.command == "stop" else "running"
        print(json.dumps(request_json(arguments.leader, "POST", "/v1/control", {"state": state})))

    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
