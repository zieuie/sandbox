"""Disk usage per machine, split into tiles / other king_hamming data / unrelated / free.

The leader only knows each node's free bytes as of its last heartbeat, and nothing
once agents stop. So the dashboard measures the machines itself, in a background
thread that is independent of snapshot builds: one fixed script per host (over
SSH, or locally for this machine), classified against the leader's database. Page
loads only read the last result; they never start a measurement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Callable

DEFAULT_INTERVAL = 1800.0
MIN_GAP = 60.0                    # operators can ask for a measurement at most this often
HOST_TIMEOUT = 240.0
TARGET_COPIES = 3
TOP_FIELDS = 6
# An agent's blob store, as the leader records it for each node.
STORAGE_ROOT = re.compile(r"/home/[A-Za-z0-9_.-]+/\.local/share/king_hamming/[A-Za-z0-9._-]+/blobs")
GROUPS = ("finished", "unfinished", "failed")

# Runs on the machine being measured (python3 - BASE BLOBS [EXTRA...]); standard library only.
# Every file is counted once by allocated bytes, so hard links (the dependency
# cache shares inodes with blobs) are never double counted. Blobs are visited
# first, so a shared inode is attributed to the blob store.
REMOTE_SCRIPT = r'''
import json, os, sys

base, blobs = sys.argv[1], sys.argv[2]
extras = sys.argv[3:]
deploy = os.path.dirname(blobs)
work = os.path.join(deploy, "work")
seen = set()
errors = 0


def tree(top, listing=None, skip=()):
    """Sum allocated bytes under top once per inode, pruning skip; optionally list canonical blob files."""
    global errors
    total = 0
    for directory, directories, names in os.walk(top, followlinks=False, onerror=lambda e: None):
        directories[:] = [d for d in directories if os.path.join(directory, d) not in skip]
        canonical = (listing is not None and os.path.dirname(directory) == top
                     and len(os.path.basename(directory)) == 2)
        for name in names:
            path = os.path.join(directory, name)
            try:
                status = os.lstat(path)
            except OSError:
                errors += 1
                continue
            if status.st_nlink > 1:
                key = (status.st_dev, status.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            size = status.st_blocks * 512
            total += size
            if canonical and len(name) == 62:
                listing.append([os.path.basename(directory) + name, size])
    return total


listing = []
result = {"blobs_bytes": tree(blobs, listing), "blobs": listing}
result["work_bytes"] = tree(work)
result["deployment_bytes"] = tree(deploy, skip=(blobs, work)) if os.path.isdir(deploy) else 0
others = 0
for name in os.listdir(base):
    path = os.path.join(base, name)
    if path != deploy and os.path.isdir(path) and not os.path.islink(path):
        others += tree(path)
result["other_deployments_bytes"] = others
result["extra_bytes"] = sum(tree(path) for path in extras if os.path.isdir(path))
status = os.statvfs(base)
result["size"] = status.f_blocks * status.f_frsize
result["free"] = status.f_bfree * status.f_frsize
result["available"] = status.f_bavail * status.f_frsize
result["errors"] = errors
print(json.dumps(result, separators=(",", ":")))
'''


class MeasureError(Exception):
    """A host could not be measured; the message says why."""


def valid_root(root: Any) -> bool:
    return isinstance(root, str) and STORAGE_ROOT.fullmatch(root) is not None


def run_script(command: list[str]) -> dict:
    """Run REMOTE_SCRIPT through command (reading it on stdin) and parse its JSON line."""
    try:
        done = subprocess.run(command, input=REMOTE_SCRIPT, capture_output=True, text=True,
                              timeout=HOST_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise MeasureError("timed out") from None
    except OSError as error:
        raise MeasureError(str(error)) from None
    if done.returncode != 0:
        lines = (done.stderr or "").strip().splitlines()
        raise MeasureError(lines[-1][:200] if lines else f"exit {done.returncode}")
    try:
        value = json.loads(done.stdout)
    except ValueError:
        raise MeasureError("unreadable answer") from None
    return value


def ssh_runner(local_host: str, local_extras: tuple[str, ...]) -> Callable[[str, str], dict]:
    """Measure host: locally for this machine, otherwise over SSH with a fixed command."""

    def run(host: str, root: str) -> dict:
        if not valid_root(root):
            raise MeasureError("unexpected storage path")
        base = str(Path(root).parents[1])
        if host == local_host:
            return run_script([sys.executable, "-", base, root, *local_extras])
        remote = f"python3 - {shlex.quote(base)} {shlex.quote(root)}"
        return run_script(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                           "-o", "ServerAliveInterval=10", host, remote])

    return run


def whole(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**62


def check_raw(raw: Any) -> dict:
    """Refuse an answer that is not exactly the shape the script produces."""
    keys = ("blobs_bytes", "work_bytes", "deployment_bytes", "other_deployments_bytes", "extra_bytes",
            "size", "free", "available", "errors")
    if (not isinstance(raw, dict) or not all(whole(raw.get(key)) for key in keys) or
            not isinstance(raw.get("blobs"), list) or
            not all(isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
                    and len(item[0]) == 64 and whole(item[1]) for item in raw["blobs"])):
        raise MeasureError("unexpected answer")
    if raw["size"] == 0 or raw["available"] > raw["size"] or raw["free"] > raw["size"]:
        raise MeasureError("implausible filesystem numbers")
    return raw


# ----- classification against the leader's database ---------------------------

def tile_index(connection: sqlite3.Connection) -> dict:
    """Map every tile packet and band hash to ('finished'|'unfinished'|'failed', (p, r)); add copy statistics.

    A packet shared by several attempts (reused tiles) counts as finished if any
    referencing root completed, else unfinished if any is still active, else failed.
    """

    rank = {"finished": 0, "unfinished": 1, "failed": 2}
    groups: dict[str, tuple[str, tuple[int, int]]] = {}
    for digest, p, r, state in connection.execute(
            "SELECT r.artifact_hash,json_extract(r.specification,'$.arguments.p'),"
            "json_extract(r.specification,'$.arguments.r'),parent.state "
            "FROM runs r LEFT JOIN runs parent ON parent.run_id=r.parent_run_id "
            "WHERE r.artifact_hash IS NOT NULL AND json_extract(r.specification,'$.program')='dp_tile'"):
        group = ("finished" if state == "complete" else "failed" if state in ("failed", "cancelled")
                 else "unfinished")
        if digest not in groups or rank[group] < rank[groups[digest][0]]:
            groups[digest] = (group, (p, r))
    try:
        for band, packet in connection.execute("SELECT band_hash,packet_hash FROM tile_bands"):
            if packet in groups:
                groups[band] = groups[packet]
    except sqlite3.OperationalError:
        pass  # a leader from before edge bands
    unique = excess = count = copies = 0
    for digest, size, target, held in connection.execute(
            "SELECT a.artifact_hash,a.size,a.target_replicas,"
            "(SELECT COUNT(*) FROM replicas x WHERE x.artifact_hash=a.artifact_hash) FROM artifacts a"):
        if digest in groups:
            size = size or 0
            unique += size
            excess += size * max(0, held - (target or TARGET_COPIES))
            count += 1
            copies += held
    return {"groups": groups, "unique_bytes": unique, "excess_bytes": excess,
            "average_copies": round(copies / count, 2) if count else None, "tiles": count}


def breakdown(raw: dict, groups: dict[str, tuple[str, tuple[int, int]]], measured_at: float) -> dict:
    """Split one machine's measurement into the four categories and the detail behind them."""

    by_group = {group: 0 for group in GROUPS}
    by_field: dict[tuple[int, int], int] = {}
    listed = 0
    for digest, size in raw["blobs"]:
        listed += size
        known = groups.get(digest)
        if known:
            by_group[known[0]] += size
            by_field[known[1]] = by_field.get(known[1], 0) + size
    tiles = sum(by_group.values())
    kept = (raw["blobs_bytes"] + raw["work_bytes"] + raw["deployment_bytes"] +
            raw["other_deployments_bytes"] + raw["extra_bytes"])
    other = max(0, kept - tiles)
    free = raw["available"]
    unrelated = max(0, raw["size"] - free - tiles - other)
    top = sorted(by_field.items(), key=lambda item: -item[1])[:TOP_FIELDS]
    return {
        "size": raw["size"], "free": free, "tiles": tiles, "other": other, "unrelated": unrelated,
        "reserved": max(0, raw["free"] - raw["available"]),
        "tiles_by_group": by_group,
        "top_fields": [[p, r, size] for (p, r), size in top],
        "other_parts": {"blobs": max(0, raw["blobs_bytes"] - tiles), "scratch": raw["work_bytes"],
                        "deployments": raw["other_deployments_bytes"] + raw["deployment_bytes"],
                        "repository": raw["extra_bytes"]},
        "unlisted_blobs": max(0, raw["blobs_bytes"] - listed),
        "errors": raw["errors"], "measured_at": measured_at,
    }


def summarize(hosts: dict[str, dict], index: dict | None) -> dict:
    """Add the machines up, with the figures items 22–24 of CAMPAIGN_NOTES would reclaim."""

    live = [item for item in hosts.values() if item.get("size")]
    total = {key: sum(item[key] for item in live) for key in ("size", "free", "tiles", "other", "unrelated")}
    total["tiles_by_group"] = {group: sum(item["tiles_by_group"][group] for item in live) for group in GROUPS}
    total["machines"] = len(live)
    reclaim = {"finished_tiles": total["tiles_by_group"]["finished"],
               "scratch": sum(item["other_parts"]["scratch"] for item in live),
               "excess_copies": index["excess_bytes"] if index else None}
    return {"total": total, "reclaimable": reclaim, "target_copies": TARGET_COPIES,
            "average_copies": index["average_copies"] if index else None,
            "unique_tile_bytes": index["unique_bytes"] if index else None}


# ----- the background monitor --------------------------------------------------

class DiskMonitor:
    """Measure every machine on a schedule and on request; keep the latest result on disk."""

    def __init__(self, state_dir: Path, deployments: Path, campaign: str, hosts: list[str],
                 runner: Callable[[str, str], dict], interval: float = DEFAULT_INTERVAL,
                 clock: Callable[[], float] = time.time, on_change: Callable[[], None] | None = None,
                 min_gap: float = MIN_GAP) -> None:
        self.path = state_dir / "disk.json"
        self.database = deployments / campaign / "leader.sqlite"
        self.hosts = list(hosts)
        self.runner = runner
        self.interval = interval
        self.clock = clock
        self.on_change = on_change
        self.min_gap = min_gap
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.last_request = 0.0
        self.results: dict[str, dict] = {}
        self.cluster: dict | None = None
        self.measured_at: float | None = None
        self.load()

    # persistence: a restart shows the previous numbers at once, labelled with their age
    def load(self) -> None:
        try:
            stored = json.loads(self.path.read_text())
            self.results = stored["hosts"]
            self.cluster = stored.get("cluster")
            self.measured_at = stored.get("measured_at")
        except (OSError, ValueError, KeyError, TypeError):
            self.results, self.cluster, self.measured_at = {}, None, None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"hosts": self.results, "cluster": self.cluster,
                                         "measured_at": self.measured_at}, separators=(",", ":")))
        os.replace(temporary, self.path)

    # what the snapshot shows
    def view(self) -> dict:
        with self.lock:
            return {"hosts": json.loads(json.dumps(self.results)), "cluster": self.cluster,
                    "measured_at": self.measured_at, "interval": self.interval, "running": self.running}

    def request(self) -> dict:
        """Ask for a measurement now; refuse while one is running or one began a moment ago."""
        with self.lock:
            now = self.clock()
            if self.running:
                return {"started": False, "reason": "a measurement is already running"}
            wait = self.min_gap - (now - self.last_request)
            if wait > 0:
                return {"started": False, "reason": "measured a moment ago", "retry_after": round(wait, 1)}
            self.last_request = now
        self.wake.set()
        return {"started": True}

    def storage_roots(self, connection: sqlite3.Connection) -> dict[str, str]:
        roots = {}
        for address, root in connection.execute("SELECT address,storage_root FROM nodes"):
            host = address.split("//")[-1].rsplit(":", 1)[0]
            roots[host] = root
        return roots

    def measure(self) -> None:
        """Measure every host, one at a time, and publish the result."""
        with self.lock:
            self.running = True
            self.last_request = self.clock()
        self.notify()
        try:
            try:
                connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=10)
                try:
                    roots = self.storage_roots(connection)
                    index = tile_index(connection)
                finally:
                    connection.close()
            except sqlite3.Error as error:
                roots, index = {}, None
                failure = f"leader database: {error}"
            else:
                failure = None
            fresh: dict[str, dict] = {}
            for host in self.hosts:
                started = self.clock()
                try:
                    if failure:
                        raise MeasureError(failure)
                    if host not in roots:
                        raise MeasureError("not registered with the leader")
                    raw = check_raw(self.runner(host, roots[host]))
                    fresh[host] = breakdown(raw, index["groups"], self.clock())
                except MeasureError as error:
                    previous = self.results.get(host, {})
                    fresh[host] = {**previous, "error": str(error), "error_at": self.clock()}
                except Exception as error:  # one bad host must never end the monitor
                    fresh[host] = {**self.results.get(host, {}), "error": f"{type(error).__name__}: {error}",
                                   "error_at": self.clock()}
                fresh[host]["seconds"] = round(self.clock() - started, 1)
            with self.lock:
                self.results = fresh
                good = {host: item for host, item in fresh.items() if "error" not in item}
                if good:
                    self.measured_at = self.clock()
                self.cluster = summarize({host: item for host, item in fresh.items() if item.get("size")}, index)
                self.save()
        finally:
            with self.lock:
                self.running = False
            self.notify()

    def notify(self) -> None:
        if self.on_change:
            self.on_change()

    def loop(self) -> None:
        stale = (self.measured_at is None or self.clock() - self.measured_at >= self.interval
                 or any(host not in self.results for host in self.hosts))
        delay = 5.0 if stale else max(5.0, self.interval - (self.clock() - self.measured_at))
        while True:
            woke = self.wake.wait(delay)
            self.wake.clear()
            try:
                self.measure()
            except Exception as error:  # keep the thread alive; the next round retries
                print(f"disk monitor: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            delay = self.interval

    def start(self) -> None:
        threading.Thread(target=self.loop, name="disk-monitor", daemon=True).start()
