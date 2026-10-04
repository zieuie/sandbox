"""Build read-only dashboard snapshots from retained campaign state.

Nothing here writes to a campaign or contacts a leader. Every database is
opened with SQLite's read-only URI mode, and one build reads the live campaign
inside a single transaction so all views describe the same instant.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import threading
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "cluster"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from campaigns.result_table import certificate_status, permutation_count  # noqa: E402
from dp_solver.artifacts import decode_dp  # noqa: E402
from dp_solver.tiles import dependencies, tile  # noqa: E402
from matching_solver.adapter import decode_input  # noqa: E402
from matching_solver.artifacts import request_count  # noqa: E402
import leader  # noqa: E402  (idle-reason rules stay owned by the leader)
from feeder import build_feeder  # noqa: E402
from logs import FeederLogParser, LeaderLogParser, LogWatcher  # noqa: E402
from matching import build_matching  # noqa: E402
from problems import build_problems  # noqa: E402
from timeline import build_timeline  # noqa: E402

# Hostnames from docs/CLUSTER_INVENTORY.md; .151 reports `uther` but is merlin.
HOST_NAMES = {
    "192.168.4.101": "fearless", "192.168.4.102": "red", "192.168.4.103": "lover",
    "192.168.4.104": "folklore", "192.168.4.105": "evermore", "192.168.4.106": "midnights",
    "192.168.4.107": "poets", "192.168.4.108": "showgirl", "192.168.4.151": "merlin",
    "192.168.4.152": "pellinore", "192.168.4.156": "gawain",
}
ACTIVE = {"queued", "waiting", "running", "stopping", "paused"}
TERMINAL = {"complete", "failed", "cancelled"}
DP_PROGRAMS = {"dp", "dp_distributed"}
MATCH_PROGRAMS = {"match", "match_distributed", "match_partitioned", "match_gpu", "match_gpu_blocks"}
WINDOW_SECONDS = 24 * 3600
BUCKET_SECONDS = 15 * 60
LONG_WINDOW_SECONDS = 7 * 24 * 3600
LONG_MERGE_GAP = 600.0  # under a pixel at 7-day scale
MAX_SAMPLE_GAP = 600.0


def open_read_only(database: Path) -> sqlite3.Connection:
    """Open database without any ability to modify it."""
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def setting(connection: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return default if row is None else row[0]


def host_of(address: str) -> str:
    """Return the bare host from an agent storage URL such as http://h:port."""
    return address.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]


def cpu_list(value: str | None) -> list[int]:
    return [int(item) for item in (value or "").split(",") if item.strip()]


def solver_health(run: dict, now: float) -> str:
    """Mirror the leader's status labels for one run row."""
    if run["state"] != "running":
        return run["state"]
    if run.get("stop_requested"):
        return "stopping"
    heartbeat = run.get("last_solver_heartbeat")
    if heartbeat is None:
        started = run.get("started")
        return "heartbeat-missing" if started is not None and now - started > 30 else "starting"
    if now - heartbeat > 30:
        return "heartbeat-missing"
    progress = run.get("last_progress_at")
    if progress is not None and now - progress >= 1800:
        return "stalled"
    if progress is not None and now - progress >= 300:
        return "no-progress-warning"
    return "responding"


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for low, high in sorted(intervals):
        if end is None or low > end:
            total += high - low
            end = high
        elif high > end:
            total += high - end
            end = high
    return total


def node_gpus(node: dict) -> list[dict] | None:
    """Decode a node's advertised GPUs (gpus_json); None when the leader predates GPU support."""
    if "gpus_json" not in node:
        return None
    try:
        devices = json.loads(node.get("gpus_json") or "[]")
    except ValueError:
        return []
    return [{"index": item.get("index"), "name": str(item.get("name", "")),
             "total_bytes": int(item.get("total_bytes") or 0)}
            for item in devices if isinstance(item, dict)]


class Snapshots:
    """Cache one snapshot, rebuilt on demand after ttl seconds.

    Expensive, immutable inputs (DP artifacts, matching certificates, tile
    dependency geometry) are memoized across builds, so a rebuild costs only
    the live database queries.
    """

    def __init__(self, deployments: Path, campaign: str = "continuous-campaign",
                 ttl: float = 15.0, min_refresh: float = 3.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.deployments = deployments
        self.campaign = campaign
        self.ttl = ttl
        self.min_refresh = min_refresh
        self.clock = clock
        self.lock = threading.Lock()
        self.encoded: tuple[bytes, bytes] | None = None
        self.snapshot: dict | None = None
        self.built_at = 0.0
        self.previous: dict[str, Any] = {}
        self.dp_files: dict[tuple, tuple[dict, str]] = {}
        self.certificates: dict[tuple, int] = {}
        self.match_inputs: dict[str, tuple[dict, bytes]] = {}
        self.dependency_cache: dict[tuple, list[tuple[int, int]]] = {}
        self.descriptions: dict[str, dict] = {}
        self.long_utilization: tuple[float, dict] | None = None  # (computed at, 7-day hourly series)
        self.disk = None  # a disk.DiskMonitor; its latest result is merged into the fleet, never measured here
        state = deployments / campaign
        self.leader_log = LogWatcher(state / "leader.log", LeaderLogParser())
        self.feeder_log = LogWatcher(state / "feeder.log", FeederLogParser())

    # ----- caching -------------------------------------------------------

    def get(self, force: bool = False) -> tuple[dict, bytes, bytes]:
        """Return (snapshot, JSON bytes, gzip bytes), rebuilding when stale."""
        with self.lock:
            age = self.clock() - self.built_at
            if self.snapshot is None or age >= self.ttl or (force and age >= self.min_refresh):
                self.snapshot = self.build()
                body = json.dumps(self.snapshot, separators=(",", ":")).encode()
                self.encoded = (body, gzip.compress(body, 6))
                self.built_at = self.clock()
            return self.snapshot, *self.encoded

    def invalidate(self) -> None:
        """Make the next request rebuild (after a command changed something)."""
        with self.lock:
            self.built_at = 0.0

    def age(self) -> float | None:
        return None if self.snapshot is None else self.clock() - self.built_at

    def build(self) -> dict:
        started = self.clock()
        snapshot: dict[str, Any] = {"generated_at": started, "campaign": self.campaign,
                                    "warnings": [], "stale": []}
        warnings = snapshot["warnings"]
        state = self.deployments / self.campaign
        try:
            connection = open_read_only(state / "leader.sqlite")
        except sqlite3.Error as error:
            connection = None
            warnings.append({"section": "campaign", "message": f"cannot open leader database: {error}"})
        for watcher in (self.leader_log, self.feeder_log):
            try:
                watcher.poll(started)
            except OSError as error:
                warnings.append({"section": "logs", "message": f"{watcher.path.name}: {error}"})

        def run(name, build, needs_database=True):
            try:
                if needs_database and connection is None:
                    raise RuntimeError("leader database unavailable")
                snapshot[name] = build()
                self.previous[name] = snapshot[name]
            except Exception as error:  # keep serving the last good section
                warnings.append({"section": name, "message": f"{type(error).__name__}: {error}"})
                snapshot[name] = self.previous.get(name)
                snapshot["stale"].append(name)

        try:
            if connection is not None:
                connection.execute("BEGIN")  # one consistent WAL read snapshot
            run("status", lambda: self.build_status(connection, state, started))
            run("fleet", lambda: self.build_fleet(connection, started, warnings))
            run("roots", lambda: self.build_roots(connection, state, started))
            run("timeline", lambda: self.build_timeline(connection, snapshot, started))
            run("matching", lambda: build_matching(
                connection, {node["name"]: node["hostname"] for node in (snapshot["fleet"] or {}).get("nodes", [])},
                self.describe_run, solver_health, started))
            run("feeder", lambda: build_feeder(state, self.feeder_log, snapshot["roots"], started),
                needs_database=False)
            run("problems", lambda: build_problems(snapshot, connection, started, self.describe_run,
                                                   self.leader_log, self.feeder_log))
        finally:
            if connection is not None:
                connection.close()  # ends the read transaction before slower file work
        run("results", lambda: self.build_results(started, warnings), needs_database=False)
        snapshot["build_seconds"] = round(self.clock() - started, 3)
        return snapshot

    # ----- header --------------------------------------------------------

    def build_status(self, connection: sqlite3.Connection, state: Path, now: float) -> dict:
        lease_seconds = float(setting(connection, "lease_seconds", 60))
        counts = dict(connection.execute(
            "SELECT state,COUNT(*) FROM runs WHERE state IN ('queued','waiting','running') "
            "GROUP BY state").fetchall())
        heartbeats = [row[0] for row in connection.execute("SELECT last_heartbeat FROM nodes")]
        status = {
            "dispatch": setting(connection, "campaign_state", "unknown"),
            "schema_version": setting(connection, "schema_version"),
            "lease_seconds": lease_seconds,
            "nodes_total": len(heartbeats),
            "nodes_healthy": sum(now - beat <= lease_seconds for beat in heartbeats),
            "runs": {key: counts.get(key, 0) for key in ("running", "queued", "waiting")},
            "feeder": None,
        }
        pipeline_path = state / "pipeline.json"
        if pipeline_path.exists():
            pipeline = json.loads(pipeline_path.read_text())
            status["feeder"] = {
                "state": pipeline.get("feeder_state"),
                "policy": pipeline.get("policy", "legacy"),
                "last_reconcile": pipeline.get("last_reconcile"),
                "demand": pipeline.get("demand", {}),
            }
        return status

    # ----- run labels ----------------------------------------------------

    def match_input(self, specification: dict, key: str) -> tuple[dict, bytes]:
        if key not in self.match_inputs:
            dp, _, digest = decode_input(specification)
            self.match_inputs[key] = (dp, digest)
        return self.match_inputs[key]

    def build_timeline(self, connection: sqlite3.Connection, snapshot: dict, now: float) -> dict:
        """24 h of leases in detail, plus a 7-day overview with small gaps merged and
        hourly CPU use (recomputed at most every 5 minutes; it changes slowly)."""
        names = [node["name"] for node in (snapshot["fleet"] or {}).get("nodes", [])]
        timeline = build_timeline(connection, names, self.describe_run, now)
        overview = build_timeline(connection, names, self.describe_run, now,
                                  window=LONG_WINDOW_SECONDS, merge_gap=LONG_MERGE_GAP)
        if self.long_utilization is None or now - self.long_utilization[0] > 300:
            nodes = [dict(row) for row in connection.execute("SELECT node_name,cpu_set FROM nodes")]
            series, _ = self.utilization(connection, nodes, now, LONG_WINDOW_SECONDS, 3600)
            self.long_utilization = (now, series)
        oldest = connection.execute("SELECT MIN(started) FROM lease_history").fetchone()[0]
        timeline["overview"] = {"start": overview["start"], "nodes": overview["nodes"],
                                "segments": overview["segments"],
                                "utilization": self.long_utilization[1], "bucket_seconds": 3600,
                                "computed_at": self.long_utilization[0]}
        timeline["history_start"] = oldest
        return timeline

    def describe_run(self, run: dict) -> dict:
        """describe(), memoized by run id; a run's specification never changes."""
        key = run["run_id"]
        if key not in self.descriptions:
            if len(self.descriptions) > 200_000:
                self.descriptions.clear()
            self.descriptions[key] = self.describe(run)
        return self.descriptions[key]

    def describe(self, run: dict) -> dict:
        """Return a compact, human-oriented description of one run."""
        try:
            specification = json.loads(run["specification"])
        except (TypeError, ValueError):
            return {"kind": "unknown", "field": None, "label": "unreadable specification"}
        program = specification.get("program", "unknown")
        arguments = specification.get("arguments", {})
        if program == "dp_tile":
            return {"kind": "dp_tile", "field": [arguments["p"], arguments["r"]],
                    "label": f"tile {arguments['row']},{arguments['column']}",
                    "tile": [arguments["row"], arguments["column"]],
                    "parent_run_id": arguments.get("parent_run_id")}
        if program in DP_PROGRAMS:
            return {"kind": program, "field": [arguments["p"], arguments["r"]],
                    "label": "DP root" if program == "dp_distributed" else "whole DP"}
        if program in MATCH_PROGRAMS:
            try:
                dp, _ = self.match_input(specification, run["calculation_id"])
                field = [dp["p"], dp["r"]]
            except (KeyError, ValueError):
                field = None
            return {"kind": "matching", "field": field, "label": "matching",
                    "poly": arguments.get("poly"), "program": program}
        return {"kind": program, "field": None, "label": program}

    # ----- fleet ---------------------------------------------------------

    def build_fleet(self, connection: sqlite3.Connection, now: float, warnings: list) -> dict:
        lease_seconds = float(setting(connection, "lease_seconds", 60))
        dispatch = setting(connection, "campaign_state", "unknown")
        nodes = [dict(row) for row in connection.execute("SELECT * FROM nodes ORDER BY node_name")]
        reservations = {row["node_name"]: row["run_id"] for row in
                        connection.execute("SELECT node_name,run_id FROM node_reservations")}
        for node in nodes:
            node["reserved_for"] = reservations.get(node["node_name"])
        running = [dict(row) for row in connection.execute("SELECT * FROM runs WHERE state='running'")]
        context = running + [dict(row) for row in connection.execute(
            "SELECT * FROM runs WHERE parent_run_id IS NULL AND state IN ('waiting','queued')")]
        try:
            leader.annotate_idle_reasons(connection, nodes, context, dispatch, lease_seconds, now)
        except Exception as error:
            warnings.append({"section": "fleet", "message": f"idle reasons unavailable: {error}"})
        by_id = {run["run_id"]: run for run in running}
        root_states = dict(connection.execute(
            "SELECT run_id,state FROM runs WHERE run_id IN (SELECT DISTINCT parent_run_id FROM runs "
            "WHERE state='running' AND parent_run_id IS NOT NULL)").fetchall())
        series, busy = self.utilization(connection, nodes, now)
        gpu_series, gpu_now = self.gpu_utilization(connection, nodes, now)

        cards = []
        for node in nodes:
            name = node["node_name"]
            host = host_of(node["address"])
            allocatable = cpu_list(node["cpu_set"])
            work = []
            for run in running:
                if run["node_name"] == name:
                    work.append(self.work_item(run, "lease", node, now, root_states))
            if node["reserved_for"] in by_id:
                work.append(self.work_item(by_id[node["reserved_for"]], "partner", node, now,
                                           root_states))
            cpu_owner = {}
            for index, item in enumerate(work):
                for cpu in item["cpus"]:
                    cpu_owner.setdefault(cpu, index)
            logical = max(allocatable + list(cpu_owner) + [-1]) + 1
            cpus = [{"id": cpu,
                     "state": ("busy" if cpu in cpu_owner else
                               "free" if cpu in allocatable else "reserved"),
                     "work": cpu_owner.get(cpu)} for cpu in range(logical)]
            alive = now - node["last_heartbeat"] <= lease_seconds
            cards.append({
                "name": name,
                "host": host,
                "hostname": HOST_NAMES.get(host, name),
                "state": node["state"] if alive else "unavailable",
                "heartbeat_age": now - node["last_heartbeat"],
                "compute_enabled": bool(node.get("compute_enabled", 1)),
                "idle_reason": node.get("idle_reason"),
                "memory_bytes": node.get("memory_bytes") or 0,
                "reserved_memory_bytes": sum(item["memory"] or 0 for item in work),
                "physical_cores": node.get("physical_core_count"),
                "runtime_version": (node.get("runtime_version") or "")[:12],
                "storage_validation": node.get("storage_validation_mode"),
                "gpus": node_gpus(node),
                # The leader's own last figure; negative means the agent never reported one.
                "leader_free_bytes": (node.get("storage_free_bytes")
                                      if (node.get("storage_free_bytes") or 0) >= 0 else None),
                "cpus": cpus,
                "work": work,
                "utilization": series.get(name, []),
                "gpu_utilization": gpu_series.get(name),
                "gpu_now": gpu_now.get(name),
                "busy_fraction": busy.get(name, 0.0),
            })
        order = sorted(HOST_NAMES)
        cards.sort(key=lambda card: (order.index(card["host"]) if card["host"] in order else 99,
                                     card["name"]))
        disk = None
        if self.disk is not None:
            view = self.disk.view()
            for card in cards:
                card["disk"] = view["hosts"].get(card["host"])
            disk = {key: view[key] for key in ("cluster", "measured_at", "interval", "running")}
        return {"nodes": cards, "bucket_seconds": BUCKET_SECONDS, "window_seconds": WINDOW_SECONDS,
                "dispatch": dispatch, "disk": disk}

    def work_item(self, run: dict, role: str, node: dict, now: float,
                  root_states: dict[str, str]) -> dict:
        description = self.describe(run)
        # A tile still running after its root reached a terminal state is
        # work the feeder no longer wants; surface it.
        root_state = root_states.get(run.get("parent_run_id") or "")
        if role == "partner":
            cpus = cpu_list(node["cpu_set"])
        else:
            cpus = cpu_list(run.get("assigned_cpu_set")) or cpu_list(node["cpu_set"])
        return {
            "run_id": run["run_id"], "role": role, **description,
            "phase": run.get("progress_phase"), "done": run.get("progress_done"),
            "total": run.get("progress_total"), "units": run.get("progress_units"),
            "cpus": cpus, "memory": None if role == "partner" else run.get("reserved_memory_bytes"),
            # A fenced GPU lease, or a DP tile that ran on the host GPU opportunistically.
            "gpu": run.get("gpu_index"),
            "accelerated": ("(gpu)" in (run.get("progress_message") or "") or
                            '"engine":"gpu"' in (run.get("progress_details") or "")),
            "started": run.get("started"), "health": solver_health(run, now),
            "root_state": root_state, "orphaned": root_state in TERMINAL,
        }

    def gpu_utilization(self, connection: sqlite3.Connection, nodes: list[dict], now: float,
                        window: int = WINDOW_SECONDS, bucket: int = BUCKET_SECONDS,
                        ) -> tuple[dict[str, list[float]], dict[str, dict]]:
        """Return per-node GPU utilisation buckets (0..1, averaged over devices) and the latest sample.

        Empty when the leader has no gpu_usage_samples yet (an older leader database).
        """
        start = now - window
        buckets = window // bucket
        totals = {node["node_name"]: [[0, 0] for _ in range(buckets)] for node in nodes}
        latest: dict[str, dict] = {}
        try:
            rows = connection.execute(
                "SELECT node_name,gpu_index,util_percent,memory_used_bytes,recorded FROM gpu_usage_samples "
                "WHERE recorded>? ORDER BY recorded", (start,)).fetchall()
        except sqlite3.OperationalError:
            return {}, {}
        for name, index, util, memory, recorded in rows:
            if name not in totals:
                continue
            slot = totals[name][min(buckets - 1, int((recorded - start) // bucket))]
            slot[0] += util
            slot[1] += 1
            if now - recorded <= 120:
                latest.setdefault(name, {})[index] = {"util": util, "memory_used_bytes": memory}
        series = {name: [round(total / count / 100, 4) if count else 0.0 for total, count in slots]
                  for name, slots in totals.items() if any(count for _, count in slots)}
        return series, {name: {"util": round(sum(d["util"] for d in devices.values()) / len(devices)),
                               "memory_used_bytes": sum(d["memory_used_bytes"] for d in devices.values())}
                        for name, devices in latest.items()}

    def utilization(self, connection: sqlite3.Connection, nodes: list[dict], now: float,
                    window: int = WINDOW_SECONDS, bucket: int = BUCKET_SECONDS,
                    ) -> tuple[dict[str, list[float]], dict[str, float]]:
        """Return per-node CPU utilisation buckets and the fraction of time leased."""
        start = now - window
        buckets = window // bucket
        cpu_seconds = {node["node_name"]: [0.0] * buckets for node in nodes}
        shard_leases: dict[str, set[str]] = {}
        previous = None
        for row in connection.execute(
                "SELECT lease_token,component,shard_index,node_name,cpu_microseconds,recorded "
                "FROM resource_usage_samples WHERE recorded>? "
                "ORDER BY lease_token,component,shard_index,recorded", (start - MAX_SAMPLE_GAP,)):
            identity = (row[0], row[1], row[2])
            if row[1] == "shard":
                shard_leases.setdefault(row[3], set()).add(row[0])
            if previous is not None and previous[0] == identity:
                t0, c0, t1, c1 = previous[1], previous[2], row[5], row[4]
                if 0 < t1 - t0 <= MAX_SAMPLE_GAP and c1 >= c0 and row[3] in cpu_seconds:
                    rate = (c1 - c0) / 1e6 / (t1 - t0)
                    low = max(t0, start)
                    while low < t1:
                        index = int((low - start) // bucket)
                        high = min(t1, start + (index + 1) * bucket)
                        if 0 <= index < buckets:
                            cpu_seconds[row[3]][index] += rate * (high - low)
                        low = high
            previous = (identity, row[5], row[4])

        series = {}
        for node in nodes:
            width = max(1, len(cpu_list(node["cpu_set"])))
            series[node["node_name"]] = [round(min(1.0, value / bucket / width), 4)
                                         for value in cpu_seconds[node["node_name"]]]

        intervals: dict[str, list[tuple[float, float]]] = {node["node_name"]: [] for node in nodes}
        spans = {}
        for token, name, started, finished in connection.execute(
                "SELECT lease_token,node_name,started,COALESCE(finished,?) FROM lease_history "
                "WHERE COALESCE(finished,?)>?", (now, now, start)):
            span = (max(started, start), min(finished, now))
            spans[token] = span
            if name in intervals and span[1] > span[0]:
                intervals[name].append(span)
        for name, tokens in shard_leases.items():
            for token in tokens:
                span = spans.get(token)
                if name in intervals and span and span[1] > span[0]:
                    intervals[name].append(span)
        busy = {name: round(union_seconds(values) / window, 4)
                for name, values in intervals.items()}
        return series, busy

    # ----- DP tile grids -------------------------------------------------

    def tile_dependencies(self, p: int, r: int, side: int, row: int, column: int):
        key = (p, r, side, row, column)
        if key not in self.dependency_cache:
            try:
                target = tile(p, r, side, row, column)
                self.dependency_cache[key] = [(item.row, item.column)
                                              for item in dependencies(p, r, side, target)]
            except ValueError:
                self.dependency_cache[key] = []
        return self.dependency_cache[key]

    def build_roots(self, connection: sqlite3.Connection, state: Path, now: float) -> list[dict]:
        lease_seconds = float(setting(connection, "lease_seconds", 60))
        names = {row["node_name"]: HOST_NAMES.get(host_of(row["address"]), row["node_name"])
                 for row in connection.execute("SELECT node_name,address FROM nodes")}
        candidates = [dict(row) for row in connection.execute(
            "SELECT run_id,specification,state,created,started,finished,error,priority,progress_phase,"
            "last_progress_at FROM runs "
            "WHERE parent_run_id IS NULL AND (state IN ('queued','waiting','running','stopping','paused') "
            "OR finished>?) ORDER BY created", (now - WINDOW_SECONDS,))]
        roots = []
        for root in candidates:
            specification = json.loads(root["specification"])
            if specification.get("program") == "dp_distributed":
                root["arguments"] = specification["arguments"]
                roots.append(root)
        if not roots:
            return []
        attempts = {}
        for row in connection.execute(
                "SELECT run_id,specification,created FROM runs WHERE parent_run_id IS NULL "
                "AND specification LIKE '%dp_distributed%' ORDER BY created"):
            arguments = json.loads(row["specification"]).get("arguments", {})
            attempts.setdefault((arguments.get("p"), arguments.get("r")), []).append(row["run_id"])

        identifiers = [root["run_id"] for root in roots]
        placeholders = ",".join("?" for _ in identifiers)
        cells_by_root: dict[str, list[dict]] = {identifier: [] for identifier in identifiers}
        for row in connection.execute(
                "WITH live AS (SELECT node_name FROM nodes WHERE last_heartbeat>?), "
                "copies AS (SELECT artifact_hash,COUNT(*) AS n FROM replicas JOIN live USING(node_name) "
                "GROUP BY artifact_hash) "
                "SELECT t.parent_run_id,t.row,t.column,t.child_run_id,c.state,c.node_name,c.started,"
                "c.finished,c.progress_done,c.progress_total,c.progress_phase,c.lease_attempt,c.error,"
                "c.progress_details,"
                "COALESCE(copies.n,0) AS replicas FROM distributed_tiles t "
                "LEFT JOIN runs c ON c.run_id=t.child_run_id "
                "LEFT JOIN copies ON copies.artifact_hash=c.artifact_hash "
                f"WHERE t.parent_run_id IN ({placeholders}) ORDER BY t.row,t.column",
                (now - lease_seconds, *identifiers)):
            cells_by_root[row["parent_run_id"]].append(dict(row))

        result = []
        for root in roots:
            arguments = root["arguments"]
            p, r = int(arguments["p"]), int(arguments["r"])
            side = int(arguments.get("tile_side", 4096))
            rows = cells_by_root[root["run_id"]]
            live_children = sum(item["state"] in {"running", "stopping", "queued", "waiting", "paused"}
                                for item in rows)
            active = root["state"] not in TERMINAL or live_children > 0
            # A finished field's tiles are deleted after a retention period, so their copies say nothing.
            finished = root["state"] == "complete"
            durable = {(item["row"], item["column"]) for item in rows
                       if item["state"] == "complete" and (finished or item["replicas"] >= 2)}
            counts = {key: 0 for key in ("durable", "complete", "running", "paused", "queued", "ready",
                                         "blocked", "failed", "cancelled", "unscheduled")}
            boundary, cells, durations = None, [], []
            for item in rows:
                key = (item["row"], item["column"])
                child = item["state"]
                if child == "complete":
                    status = "durable" if key in durable else "complete"
                    # Tiles reused by a retried root finish instantly; skip them.
                    if item["started"] and item["finished"] and item["finished"] - item["started"] >= 1:
                        durations.append(item["finished"] - item["started"])
                elif child in {"running", "stopping"}:
                    status = "running"
                elif child in {"queued", "waiting"}:
                    status = "queued"
                elif child == "paused":
                    status = "paused"
                elif child in {"failed", "cancelled"}:
                    status = child
                elif root["state"] in TERMINAL:
                    status = "unscheduled"
                else:
                    missing = [dependency for dependency in
                               self.tile_dependencies(p, r, side, *key) if dependency not in durable]
                    status = "blocked" if missing else "ready"
                    if missing and boundary is None:
                        boundary = f"{key[0]},{key[1]} waits for {missing[0][0]},{missing[0][1]}"
                counts[status] = counts.get(status, 0) + 1
                cell = {"r": item["row"], "c": item["column"], "s": status}
                if item["child_run_id"]:
                    cell.update(run=item["child_run_id"], rep=item["replicas"],
                                t0=item["started"], t1=item["finished"], att=item["lease_attempt"])
                    if item["node_name"]:
                        cell["node"] = names.get(item["node_name"], item["node_name"])
                    if '"engine":"gpu"' in (item["progress_details"] or ""):
                        cell["gpu"] = 1
                    if status == "running":
                        cell.update(done=item["progress_done"], total=item["progress_total"],
                                    phase=item["progress_phase"])
                    if item["error"]:
                        cell["err"] = item["error"][:300]
                cells.append(cell)
            history = attempts.get((p, r), [root["run_id"]])
            result.append({
                "run_id": root["run_id"], "p": p, "r": r, "tile_side": side,
                "state": root["state"], "active": active, "error": root["error"],
                "priority": root["priority"], "phase": root["progress_phase"],
                "last_progress_at": root["last_progress_at"],
                "orphaned_children": live_children if root["state"] in TERMINAL else 0,
                "created": root["created"], "started": root["started"], "finished": root["finished"],
                "attempt": history.index(root["run_id"]) + 1 if root["run_id"] in history else None,
                "attempts": len(history),
                "rows": 1 + max((cell["r"] for cell in cells), default=-1),
                "columns": 1 + max((cell["c"] for cell in cells), default=-1),
                "counts": counts,
                "gpu_tiles": sum(1 for cell in cells if cell.get("gpu")),
                "boundary": boundary or ("clear" if root["state"] not in TERMINAL else None),
                "median_tile_seconds": statistics.median(durations) if durations else None,
                "cells": cells,
            })
        result.sort(key=lambda item: (not item["active"], item["counts"]["running"] == 0,
                                      item["created"]))
        return result

    # ----- results table -------------------------------------------------

    def dp_file(self, path: Path, artifact_hash: str | None) -> dict:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self.dp_files:
            raw = path.read_bytes()
            self.dp_files[key] = (decode_dp(raw), hashlib.sha256(raw).hexdigest())
        dp, digest = self.dp_files[key]
        if artifact_hash and digest != artifact_hash:
            raise ValueError(f"DP artifact hash mismatch: {path.name}")
        return dp

    def certificate(self, path: Path, artifact_hash: str, dp: dict, digest: bytes) -> int:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns, artifact_hash)
        if key not in self.certificates:
            self.certificates[key] = certificate_status(path, artifact_hash, dp, digest)
        return self.certificates[key]

    def build_results(self, now: float, warnings: list) -> dict:
        fields: dict[tuple[int, int], dict] = {}
        deployments = []

        def record(key):
            return fields.setdefault(key, {
                "p": key[0], "r": key[1], "metrics": None, "dp_attempts": [],
                "matching_attempts": [], "outcomes": set(), "admission": None,
                "notes": [], "deployments": set()})

        def set_metrics(entry, dp):
            if entry["metrics"] is None:
                count = request_count(dp)
                entry["metrics"] = {"q": dp["q"], "theta": dp["theta"], "f": dp["f"],
                                    "rows": permutation_count(dp), "requests": count,
                                    "edges": count * dp["f"]}
            elif entry["metrics"]["rows"] != permutation_count(dp):
                entry["notes"].append("conflicting DP results across deployments")

        for database in sorted(self.deployments.glob("*/leader.sqlite")):
            directory = database.parent
            try:
                connection = open_read_only(database)
            except sqlite3.Error as error:
                warnings.append({"section": "results", "message": f"{directory.name}: {error}"})
                continue
            try:
                lease_seconds = float(setting(connection, "lease_seconds", 60))
                newest = connection.execute("SELECT MAX(last_heartbeat) FROM nodes").fetchone()[0]
                live = newest is not None and now - newest <= lease_seconds
                deployments.append({"name": directory.name, "live": live,
                                    "dispatch": setting(connection, "campaign_state")})
                columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
                where = " WHERE parent_run_id IS NULL" if "parent_run_id" in columns else ""
                for run in connection.execute(
                        "SELECT run_id,calculation_id,specification,state,artifact_hash,created,"
                        "finished,error FROM runs" + where + " ORDER BY created"):
                    run = dict(run)
                    try:
                        specification = json.loads(run["specification"])
                    except ValueError:
                        continue
                    program = specification.get("program")
                    state = run["state"]
                    if state in ACTIVE and not live:
                        state = f"{state} (leader offline)"
                    attempt = {"deployment": directory.name, "run_id": run["run_id"], "state": state,
                               "active": live and run["state"] in ACTIVE, "created": run["created"],
                               "finished": run["finished"],
                               "error": (run["error"] or "")[:300] or None}
                    if program in DP_PROGRAMS:
                        arguments = specification["arguments"]
                        key = (int(arguments["p"]), int(arguments["r"]))
                        entry = record(key)
                        entry["deployments"].add(directory.name)
                        entry["dp_attempts"].append(attempt)
                        if run["state"] == "complete":
                            path = directory / "results" / f"{key[0]}_{key[1]}_{run['run_id']}.khdp"
                            try:
                                set_metrics(entry, self.dp_file(path, run["artifact_hash"]))
                            except FileNotFoundError:
                                entry["notes"].append(f"{directory.name}: completed DP not collected")
                            except ValueError as error:
                                entry["notes"].append(f"{directory.name}: {error}")
                    elif program in MATCH_PROGRAMS:
                        try:
                            dp, digest = self.match_input(specification, run["calculation_id"])
                        except (KeyError, ValueError) as error:
                            warnings.append({"section": "results",
                                             "message": f"{directory.name} {run['run_id'][:8]}: {error}"})
                            continue
                        key = (dp["p"], dp["r"])
                        entry = record(key)
                        entry["deployments"].add(directory.name)
                        set_metrics(entry, dp)
                        attempt["poly"] = specification["arguments"].get("poly")
                        attempt["program"] = program
                        if run["state"] == "complete":
                            name = f"{key[0]}_{key[1]}_{run['run_id']}.khmatch"
                            path = directory / "matching-results" / name
                            if not path.is_file():
                                path = directory / "results" / name
                            try:
                                outcome = self.certificate(path, run["artifact_hash"], dp, digest)
                                attempt["outcome"] = "matched" if outcome == 0 else "obstructed"
                                entry["outcomes"].add(outcome)
                            except FileNotFoundError:
                                entry["notes"].append(f"{directory.name}: certificate not collected")
                            except ValueError as error:
                                entry["notes"].append(f"{directory.name}: {error}")
                        entry["matching_attempts"].append(attempt)
            finally:
                connection.close()

            pipeline_path = directory / "pipeline.json"
            if pipeline_path.exists():
                try:
                    pipeline = json.loads(pipeline_path.read_text())
                except ValueError as error:
                    warnings.append({"section": "results", "message": f"{directory.name}: {error}"})
                    continue
                for item in pipeline.get("fields", {}).values():
                    key = (int(item["p"]), int(item["r"]))
                    if key not in fields:
                        continue
                    entry = fields[key]
                    if item.get("matching_admission"):
                        entry["admission"] = item["matching_admission"]
                    if item.get("matching_failure"):
                        entry["notes"].append(f"matching gave up: {item['matching_failure']}")
                    if item.get("candidate_exhausted"):
                        entry["notes"].append("every primitive polynomial tried")

        rows = []
        for key, entry in fields.items():
            entry["status"] = self.field_status(entry)
            entry["outcomes"] = sorted(entry["outcomes"])
            entry["deployments"] = sorted(entry["deployments"])
            rows.append(entry)
        rows.sort(key=lambda item: (item["p"], item["r"]))
        return {"fields": rows,
                "primes": sorted({item["p"] for item in rows}),
                "exponents": sorted({item["r"] for item in rows}),
                "deployments": deployments}

    @staticmethod
    def field_status(entry: dict) -> str:
        if 0 in entry["outcomes"]:
            return "matched"
        if any(attempt["active"] for attempt in entry["matching_attempts"]):
            return "matching"
        if 1 in entry["outcomes"]:
            return "obstructed"
        if entry["metrics"] is not None:
            admission = entry["admission"]
            if admission and admission not in {"admitted", "waiting for nodes"}:
                return "too_big"
            return "awaiting_matching"
        if any(attempt["active"] for attempt in entry["dp_attempts"]):
            return "dp_running"
        if any(attempt["state"] == "failed" for attempt in entry["dp_attempts"]):
            return "dp_failed"
        if any(attempt["state"] == "cancelled" for attempt in entry["dp_attempts"]):
            return "dp_cancelled"
        return "unknown"
