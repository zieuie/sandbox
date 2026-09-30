#!/usr/bin/env python3
"""Reconcile matching attempts, archive certificates, and retry verified obstructions."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from deployment import request
from matching_solver.artifacts import file_hash, load_dp, request_count
from matching_solver.launch_overnight import LEADER, RESULTS, STATE, MAX_BYTES, THREADS, memory_required, repair, save
from matching_solver.polynomials import next_primitive
from matching_solver.submit import specification

MANIFEST = STATE / "manifest.json"
ARCHIVE = STATE / "results"
DP_SYNC = STATE / "dp-sync.json"


# Store the verified result bytes centrally, retaining the leader's content hash.
def archive_result(entry: dict, run: dict) -> Path:
    """Return a locally archived KHM1 path with a verified SHA-256."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    output = ARCHIVE / f"{entry['p']}_{entry['r']}_{run['run_id']}.khmatch"
    digest = run["artifact_hash"]
    if output.exists():
        if file_hash(output).hex() != digest:
            raise ValueError(f"archived certificate checksum differs: {output}")
        return output
    location = run.get("artifact_location")
    if not location:
        raise ValueError("complete run has no reachable artifact location")
    dp, _ = load_dp(Path(entry["dp"]))
    count = request_count(dp)
    # A full matching has one packed choice per request. Allow one Hall bit
    # per request as well, plus a generous fixed header bound.
    maximum = (count * (dp["f"] + 1).bit_length() + 7) // 8 + (count + 7) // 8 + 4096
    temporary = output.with_name(output.name + ".tmp-" + uuid.uuid4().hex)
    try:
        size = 0
        checksum = hashlib.sha256()
        with urlopen(location, timeout=60) as response, temporary.open("xb") as stream:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("downloaded matching certificate exceeds DP-derived limit")
                stream.write(chunk)
                checksum.update(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if checksum.hexdigest() != digest:
            raise ValueError("downloaded matching certificate has wrong hash")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


# A compact table makes run identity and encountered polynomial failures reviewable.
def render(manifest: dict, runs: dict[str, dict]) -> str:
    """Return current field and Hall-attempt tables in Markdown."""
    attempts = defaultdict(list)
    for entry in manifest["entries"]:
        attempts[(entry["p"], entry["r"])].append(entry)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Matching campaign", "", f"Updated: {updated}", "",
             f"Leader: `{manifest['leader']}`", "",
             "| Field | q | Latest state | Matched | Attempts | Certificate |",
             "| --- | ---: | --- | ---: | ---: | --- |"]
    for field, group in sorted(attempts.items(), key=lambda item: (item[1][0]["edges"], item[0])):
        latest = group[-1]
        run = runs.get(latest["run_id"], {})
        state = run.get("state", "unreported")
        if state == "failed":
            detail = str(run.get("error") or "unknown error").replace("|", "/").replace("\n", " ")
            state = f"failed: {detail[:80]}"
        matched = f"{run.get('progress_done', 0)}/{run.get('progress_total', latest['q'])}"
        if state == "complete" and run.get("progress_done", 0) < run.get("progress_total", 0):
            state = "Hall obstruction"
        certificate = latest.get("archive", "")
        link = f"[KHM1]({Path(certificate).relative_to(STATE)})" if certificate else "—"
        lines.append(f"| {field[0]}^{field[1]} | {latest['q']:,} | {state} | {matched} | {len(group)} | {link} |")
    lines.extend(["", "## Encountered polynomial obstructions", "",
                  "| Field | Polynomial | Deficiency | Certificate |",
                  "| --- | --- | ---: | --- |"])
    found = False
    for entry in manifest["entries"]:
        run = runs.get(entry["run_id"], {})
        if run.get("state") != "complete" or run.get("progress_done", 0) >= run.get("progress_total", 0):
            continue
        found = True
        deficit = run["progress_total"] - run["progress_done"]
        certificate = entry.get("archive", "")
        link = f"[KHM1]({Path(certificate).relative_to(STATE)})" if certificate else "—"
        lines.append(f"| {entry['p']}^{entry['r']} | `{','.join(map(str, entry['poly']))}` | {deficit} | {link} |")
    if not found:
        lines.append("| None encountered | — | — | — |")
    return "\n".join(lines) + "\n"


# New saved DP files join the queue without disturbing earlier attempts.
def enqueue_new(manifest: dict, known: set[tuple[int, int]]) -> int:
    """Submit newly collected admissible DP results in estimated work order."""
    candidates = []
    for path in RESULTS.glob("*.khdp"):
        dp, _ = load_dp(path)
        field = (dp["p"], dp["r"])
        edges = request_count(dp) * dp["f"]
        if field not in known and dp["q"] <= manifest.get("max_q", 20_000_000) and edges <= manifest.get("max_edges", 32_000_000_000) and memory_required(dp) <= manifest.get("max_bytes", MAX_BYTES):
            candidates.append((edges, path, dp))
    count = 0
    for edges, path, dp in sorted(candidates):
        polynomial = next_primitive(dp["p"], dp["r"], dp["q"], [0] * dp["r"] + [1])
        if polynomial is None:
            raise ValueError(f"no primitive polynomial for {dp['p']}^{dp['r']}")
        job = specification(path, ",".join(map(str, polynomial)), THREADS, MAX_BYTES)
        response = request(LEADER, "/v1/enqueue", {"specification": job})
        manifest["entries"].append({"p": dp["p"], "r": dp["r"], "q": dp["q"],
                                    "edges": edges, "poly": polynomial,
                                    "dp": str(path), **response})
        known.add((dp["p"], dp["r"]))
        save(MANIFEST, manifest)
        count += 1
    return count


# One pass is idempotent across a restart, including the enqueue-before-save window.
def _reconcile() -> dict:
    """Archive completed certificates and queue only warranted follow-up attempts."""
    manifest = json.loads(MANIFEST.read_text())
    # The public status endpoint shows only 200 runs; archive every retained run.
    database = STATE / "leader.sqlite"
    if database.exists():
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            runs = {row["run_id"]: dict(row) for row in connection.execute(
                "SELECT * FROM runs WHERE parent_run_id IS NULL"
            )}
    else:
        status = request(LEADER, "/v1/status")
        runs = {row["run_id"]: row for row in status["runs"]}
    archived = retried = 0
    for entry in manifest["entries"]:
        run = runs.get(entry["run_id"])
        if run is None or run["state"] != "complete" or entry.get("archive"):
            continue
        try:
            output = archive_result(entry, run)
        except (OSError, ValueError) as error:
            print(f"archive retry for {entry['p']}^{entry['r']}: {error}", file=sys.stderr, flush=True)
            continue
        entry["archive"] = str(output)
        save(MANIFEST, manifest)
        archived += 1
    groups = defaultdict(list)
    for entry in manifest["entries"]:
        groups[(entry["p"], entry["r"])].append(entry)
    for field, attempts in groups.items():
        latest = attempts[-1]
        run = runs.get(latest["run_id"])
        if run is None or run["state"] != "complete" or not latest.get("archive"):
            continue
        if run["progress_done"] >= run["progress_total"]:
            continue
        polynomial = next_primitive(field[0], field[1], latest["q"], latest["poly"])
        if polynomial is None:
            latest["candidate_exhausted"] = True
            save(MANIFEST, manifest)
            continue
        path = Path(latest["dp"])
        job = specification(path, ",".join(map(str, polynomial)), THREADS, MAX_BYTES)
        response = request(LEADER, "/v1/enqueue", {"specification": job})
        manifest["entries"].append({**{key: latest[key] for key in
                                      ("p", "r", "q", "edges", "dp")},
                                    "poly": polynomial, **response})
        save(MANIFEST, manifest)
        retried += 1
    added = enqueue_new(manifest, set(groups))
    document = render(manifest, runs)
    temporary = STATE / "STATUS.tmp"
    temporary.write_text(document)
    temporary.replace(STATE / "STATUS.md")
    return {"archived": archived, "retried_obstructions": retried,
            "new_dp": added, "runs": len(runs)}


# Serialize manifest read-modify-write with manual frontier extension.
def reconcile() -> dict:
    """Run one reconciliation under the campaign manifest lock."""
    with (STATE / "manifest.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _reconcile()


# Periodically collect new DP artifacts through the existing read-only campaign utility.
def collect_dp_if_due(seconds: int) -> None:
    """Attempt the source campaign collection at most once per interval."""
    now = time.time()
    try:
        prior = json.loads(DP_SYNC.read_text()).get("last_attempt", 0)
    except (OSError, ValueError):
        prior = 0
    if now - prior < seconds:
        return
    temporary = DP_SYNC.with_suffix(".tmp")
    temporary.write_text(json.dumps({"last_attempt": now}) + "\n")
    temporary.replace(DP_SYNC)
    command = [sys.executable, str(ROOT / "cluster/launch_dp.py"), "collect"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("DP collection timed out; will retry later", file=sys.stderr, flush=True)
        return
    if completed.returncode:
        print(f"DP collection failed: {completed.stderr[-2000:]}", file=sys.stderr, flush=True)
    else:
        print(completed.stdout.strip(), flush=True)


# Continue after transient leader or storage errors while keeping one active watcher.
def main() -> int:
    """Run one reconciliation pass or poll until intentionally stopped."""
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Example: python3 king_hamming/matching_solver/witch_hunt.py --interval 120"))
    parser.add_argument("--once", action="store_true", help="reconcile once and exit")
    parser.add_argument("--repair-workers", action="store_true",
                        help="reattach dead owned leader or matching agents")
    parser.add_argument("--interval", type=int, default=120, help="seconds between passes")
    parser.add_argument("--collect-dp-seconds", type=int, default=0,
                        help="periodically collect source DP campaign results; zero disables")
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    if arguments.interval < 10 or arguments.collect_dp_seconds < 0:
        parser.error("interval must be at least 10 seconds and collection interval nonnegative")
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "watcher.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another matching watcher is already running") from error
        while True:
            try:
                if arguments.repair_workers:
                    recovered = repair()
                    if recovered["recovered"] or recovered["errors"]:
                        print(json.dumps({"repair": recovered}), flush=True)
                if arguments.collect_dp_seconds:
                    collect_dp_if_due(arguments.collect_dp_seconds)
                print(json.dumps(reconcile()), flush=True)
            except Exception as error:
                if arguments.once:
                    raise
                print(f"watcher will retry: {error}", file=sys.stderr, flush=True)
            if arguments.once:
                break
            time.sleep(arguments.interval)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"witch_hunt.py: {error}", file=sys.stderr)
        raise SystemExit(1)
