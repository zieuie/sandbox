#!/usr/bin/env python3
"""Capture a bounded before/after cluster throughput and utilization canary."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from kh import request_json


def report(before: dict, after: dict, elapsed: float) -> dict:
    """Return a deterministic comparison of two status snapshots."""
    old_runs = {run["run_id"]: run for run in before.get("runs", [])}
    progress = []
    for run in after.get("runs", []):
        old = old_runs.get(run["run_id"])
        if old is None:
            continue
        delta = max(0, int(run.get("progress_done", 0)) -
                    int(old.get("progress_done", 0)))
        if delta or run.get("state") == "running":
            progress.append({
                "run_id": run["run_id"], "state": run.get("state"),
                "units": run.get("progress_units"), "completed": delta,
                "per_second": delta / elapsed if elapsed else 0,
                "tile_counts": run.get("tile_counts"),
            })
    resources = []
    for run in after.get("runs", []):
        for usage in run.get("resource_usage", []):
            resources.append({
                "run_id": run["run_id"], "node": usage["node_name"],
                "component": usage["component"], "shard": usage["shard_index"],
                "assigned_cpu_utilization": usage.get("assigned_cpu_utilization"),
                "sample_seconds": usage.get("sample_seconds"),
                "peak_rss_bytes": usage["peak_rss_bytes"],
            })
    idle = Counter(node.get("idle_reason", "unknown")
                   for node in after.get("nodes", [])
                   if node.get("idle_reason") not in {"running", "matching partner reservation"})
    return {
        "format": "KH-CLUSTER-CANARY-1", "elapsed_seconds": elapsed,
        "campaign_state": after.get("campaign_state"),
        "schema_version": after.get("schema_version"),
        "scheduler": after.get("scheduler"),
        "nodes": len(after.get("nodes", [])),
        "idle_reasons": dict(sorted(idle.items())),
        "progress": sorted(progress, key=lambda item: item["run_id"]),
        "resources": sorted(resources, key=lambda item: (
            item["node"], item["run_id"], item["component"], item["shard"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leader", default="http://127.0.0.1:8041")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("-o", "--output", type=Path)
    arguments = parser.parse_args()
    if not 1 <= arguments.seconds <= 86400:
        parser.error("--seconds must be between 1 and 86400")
    before = request_json(arguments.leader, "GET", "/v1/status")
    started = time.monotonic()
    time.sleep(arguments.seconds)
    after = request_json(arguments.leader, "GET", "/v1/status")
    document = json.dumps(report(before, after, time.monotonic() - started),
                          indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(document, end="")
    else:
        with arguments.output.open("x") as output:
            output.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
