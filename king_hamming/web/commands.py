"""Operator commands: preview, confirm, check for staleness, execute, audit.

Every command is built as a Plan from fresh state. The facts a plan depends on
are hashed into a fingerprint shown to the operator; running the command
rebuilds the plan and refuses if the fingerprint changed, so nothing executes
against state the operator has not seen. Blockers refuse outright; risky plans
need a recent password re-confirmation; destructive ones need typed text.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from snapshot import ROOT, Snapshots, open_read_only  # first: sets up the import path
from feeder import feeder_process, upcoming_fields  # noqa: E402
from jobs import Jobs, git_state  # noqa: E402

TERMINAL = {"complete", "failed", "cancelled"}
LIVE = ("queued", "waiting", "running", "stopping", "paused")
PRIORITY_RANGE = (-1000, 1000)


class CommandError(Exception):
    """A refusal or failure to report to the operator."""

    def __init__(self, message: str, status: int = 400, **extra) -> None:
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


@dataclass
class Plan:
    title: str
    summary: str
    facts: Any
    action: Callable[[], dict]
    changes: list[dict] = dataclass_field(default_factory=list)
    items: list[str] = dataclass_field(default_factory=list)
    warnings: list[str] = dataclass_field(default_factory=list)
    blockers: list[str] = dataclass_field(default_factory=list)
    confirm_text: str | None = None
    reauth: bool = False
    job: bool = False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.facts, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def describe(self, name: str) -> dict:
        return {"name": name, "title": self.title, "summary": self.summary, "changes": self.changes,
                "items": self.items, "warnings": self.warnings, "blockers": self.blockers,
                "confirm_text": self.confirm_text, "reauth": self.reauth, "job": self.job,
                "fingerprint": self.fingerprint}


@dataclass
class Context:
    deployments: Path
    campaign: str
    snapshots: Snapshots
    jobs: Jobs
    launcher: Path = ROOT / "dp_solver" / "launch_dp.py"
    python: str = sys.executable
    leader_timeout: float = 60.0

    @property
    def state(self) -> Path:
        return self.deployments / self.campaign

    def manifest(self) -> dict:
        return json.loads((self.state / "manifest.json").read_text())

    def leader(self, route: str, body: dict) -> dict:
        url = self.manifest()["leader"].rstrip("/") + route
        request = Request(url, data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.leader_timeout) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                message = json.load(error).get("error", str(error))
            except ValueError:
                message = str(error)
            raise CommandError(f"leader refused: {message}", 409) from None
        except (URLError, OSError) as error:
            raise CommandError(f"leader unreachable: {error}", 502) from None

    def database(self) -> sqlite3.Connection:
        return open_read_only(self.state / "leader.sqlite")


# ----- helpers -------------------------------------------------------------

def as_int(value, name: str) -> int:
    """Accept an integer, or a string such as "300000000000000", "3e14" or "300,000"
    (browsers lose precision on large JSON numbers)."""
    if type(value) is int:
        return value
    if isinstance(value, str):
        text = value.replace(",", "").replace("_", "").strip()
        try:
            number = Decimal(text)
        except InvalidOperation:
            number = None
        if number is not None and number.is_finite() and number == number.to_integral_value():
            return int(number)
    raise CommandError(f"{name} must be a whole number")


def ascii_field(field) -> str:
    return f"{field[0]}^{field[1]}" if field else "?"


def run_row(context: Context, run_id: str) -> dict:
    if not isinstance(run_id, str) or not run_id:
        raise CommandError("run_id is required")
    with context.database() as connection:
        row = connection.execute(
            "SELECT run_id,calculation_id,specification,state,control_state,priority,node_name,"
            "parent_run_id,progress_phase FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        raise CommandError("no such run", 404)
    return dict(row)


def live_children(context: Context, root_id: str) -> list[dict]:
    with context.database() as connection:
        return [dict(row) for row in connection.execute(
            f"SELECT run_id,calculation_id,specification,state,node_name,parent_run_id FROM runs "
            f"WHERE parent_run_id=? AND state IN ({','.join('?' for _ in LIVE)}) ORDER BY run_id",
            (root_id, *LIVE))]


def label(context: Context, run: dict) -> tuple[dict, str]:
    description = context.snapshots.describe_run(run)
    field = description.get("field")
    name = f"{ascii_field(field)} {description['label']}" if field else description["label"]
    return description, name


def cancel_many(context: Context, runs: list[dict]) -> list[dict]:
    results = []
    for run in runs:
        try:
            results.append(context.leader("/v1/run-command", {"run_id": run["run_id"], "action": "cancel"}))
        except CommandError as error:  # e.g. it finished meanwhile
            results.append({"run_id": run["run_id"], "error": error.message})
    return results


class PipelineLock:
    """The feeder's own exclusive lock; held while the dashboard edits its files."""

    def __init__(self, state: Path) -> None:
        self.path = state / "pipeline.lock"

    def __enter__(self):
        self.stream = self.path.open("a")
        fcntl.flock(self.stream, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exception) -> None:
        fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


# ----- leader commands -----------------------------------------------------

def dispatch(context: Context, params: dict, target: str) -> Plan:
    with context.database() as connection:
        state = connection.execute("SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0]
        running = [row[0] for row in connection.execute(
            "SELECT run_id FROM runs WHERE state IN ('running','stopping') ORDER BY run_id")]
    stop = target == "stopped"
    plan = Plan(
        title="Stop dispatch" if stop else "Resume dispatch",
        summary=("No new work is handed out. Running work stops at its next checkpoint and "
                 "returns to the queue; nothing is lost." if stop else
                 "The leader starts handing out queued work again."),
        facts={"state": state, "running": running},
        action=lambda: context.leader("/v1/control", {"state": target}),
        changes=[{"label": "Dispatch", "before": state, "after": target}],
        confirm_text="stop" if stop else None)
    if state == target:
        plan.blockers.append(f"Dispatch is already {target}.")
    if stop and running:
        plan.warnings.append(f"{len(running)} running lease(s) will be asked to stop.")
    return plan


def run_control(context: Context, params: dict, action: str) -> Plan:
    run = run_row(context, params.get("run_id"))
    description, name = label(context, run)
    kind = description["kind"]
    facts = {"run": run["run_id"], "state": run["state"], "priority": run["priority"]}
    titles = {"pause": "Pause", "resume": "Resume", "cancel": "Cancel"}
    plan = Plan(title=f"{titles[action]} {name}", summary="", facts=facts,
                action=lambda: context.leader("/v1/run-command",
                                              {"run_id": run["run_id"], "action": action}),
                changes=[{"label": "State", "before": run["state"],
                          "after": {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action]}])
    if run["state"] in TERMINAL:
        plan.blockers.append(f"This run is already {run['state']}.")
    if action == "resume" and run["state"] != "paused":
        plan.blockers.append("Only a paused run can be resumed.")
    if action == "pause" and run["state"] == "paused":
        plan.blockers.append("This run is already paused.")

    if kind == "dp_tile":
        root = run_row(context, run["parent_run_id"]) if run["parent_run_id"] else None
        facts["root_state"] = root and root["state"]
        if action == "cancel" and root and root["state"] not in TERMINAL:
            plan.blockers.append(
                "Cancelling one tile of an active DP root leaves the root unable to finish (it never "
                "recreates a cancelled tile). Pause the tile instead, or cancel the whole field from its root.")
        plan.summary = {"pause": "The tile stops at its next checkpoint and waits; resuming continues it.",
                        "resume": "The tile returns to the queue and continues from its checkpoint.",
                        "cancel": "The tile stops and is discarded."}[action]
    elif kind == "dp_distributed":
        children = live_children(context, run["run_id"])
        facts["children"] = [(child["run_id"], child["state"]) for child in children]
        if action == "pause":
            plan.summary = "No new tiles of this field start. Running tiles finish; resume to continue."
        elif action == "resume":
            plan.summary = "Tiles of this field start being scheduled again."
        else:
            plan.summary = ("Cancels this DP attempt and its live tiles. Finished tiles stay retained, "
                            "so a later retry reuses them.")
            plan.items = [f"{child['state']}: {label(context, child)[1]}" for child in children]
            plan.confirm_text = ascii_field(description.get("field"))
            plan.reauth = True

            def cancel_root():
                result = context.leader("/v1/run-command", {"run_id": run["run_id"], "action": "cancel"})
                return {"root": result, "tiles": cancel_many(context, children)}
            plan.action = cancel_root
    elif kind == "matching":
        plan.summary = {"pause": "Matching stops at its next checkpoint and waits.",
                        "resume": "Matching returns to the queue and continues from its checkpoint.",
                        "cancel": "Matching stops. The feeder may submit a new attempt."}[action]
        if action == "cancel":
            plan.confirm_text = ascii_field(description.get("field"))
            plan.reauth = True
    else:
        plan.summary = f"{titles[action]} this {kind} run."
    if action == "pause" and run["state"] == "running":
        plan.warnings.append("It is running now; the pause takes effect at its next checkpoint.")
    return plan


def run_priority(context: Context, params: dict) -> Plan:
    run = run_row(context, params.get("run_id"))
    priority = as_int(params.get("priority"), "priority")
    if not PRIORITY_RANGE[0] <= priority <= PRIORITY_RANGE[1]:
        raise CommandError(f"priority must be an integer from {PRIORITY_RANGE[0]} to {PRIORITY_RANGE[1]}")
    description, name = label(context, run)
    plan = Plan(title=f"Change priority of {name}",
                summary="Higher priority runs are leased first.",
                facts={"run": run["run_id"], "state": run["state"], "priority": run["priority"]},
                action=lambda: context.leader("/v1/run-command", {"run_id": run["run_id"],
                                                                  "action": "reprioritize",
                                                                  "priority": priority}),
                changes=[{"label": "Priority", "before": run["priority"], "after": priority}])
    if run["state"] in TERMINAL:
        plan.blockers.append(f"This run is already {run['state']}.")
    if priority == run["priority"]:
        plan.blockers.append("That is already its priority.")
    if description["kind"] == "dp_distributed":
        plan.warnings.append("Tiles created from now on inherit it; existing tiles keep their priority.")
    return plan


def cancel_orphans(context: Context, params: dict) -> Plan:
    root = run_row(context, params.get("run_id"))
    description, name = label(context, root)
    children = live_children(context, root["run_id"])
    plan = Plan(title=f"Cancel leftover tiles of {name}",
                summary=f"The root is {root['state']}, so these tiles' results will not be used by it. "
                        "Cancelling frees their machines.",
                facts={"root": root["run_id"], "state": root["state"],
                       "children": [(child["run_id"], child["state"]) for child in children]},
                action=lambda: {"tiles": cancel_many(context, children)},
                items=[f"{child['state']}: {label(context, child)[1]}" for child in children])
    if root["state"] not in TERMINAL:
        plan.blockers.append("The root is still active; its tiles are not leftovers.")
    if not children:
        plan.blockers.append("No tiles of this root are still live.")
    plan.warnings.append("A finished copy of a running tile could be reused by a future retry; "
                         "cancelling gives that up for these tiles.")
    return plan


def restart_root(context: Context, params: dict) -> Plan:
    """Cancel one DP attempt (if still live) and submit a fresh one that reuses its durable tiles.

    This is how a single cancelled or stuck tile gets recomputed today: the
    leader cannot requeue one tile (see CAMPAIGN_NOTES.md, item 4).
    """
    root = run_row(context, params.get("run_id"))
    description, name = label(context, root)
    if description["kind"] != "dp_distributed":
        raise CommandError("only distributed DP roots can be restarted")
    specification = json.loads(root["specification"])
    p, r = specification["arguments"]["p"], specification["arguments"]["r"]
    children = live_children(context, root["run_id"])
    with context.database() as connection:
        newer = connection.execute(
            "SELECT run_id,state FROM runs WHERE parent_run_id IS NULL AND calculation_id=? "
            "AND created>(SELECT created FROM runs WHERE run_id=?) ORDER BY created",
            (root["calculation_id"], root["run_id"])).fetchall()
        cancelled = connection.execute(
            "SELECT t.row,t.column FROM distributed_tiles t JOIN runs c ON c.run_id=t.child_run_id "
            "WHERE t.parent_run_id=? AND c.state='cancelled' ORDER BY t.row,t.column",
            (root["run_id"],)).fetchall()
    manifest = context.manifest()
    owned = any(entry.get("run_id") == root["run_id"] for entry in manifest.get("entries", []))

    def restart():
        result = {}
        if root["state"] not in TERMINAL:
            result["root"] = context.leader("/v1/run-command", {"run_id": root["run_id"], "action": "cancel"})
        result["tiles"] = cancel_many(context, children)
        with PipelineLock(context.state):
            submitted = context.leader("/v1/enqueue", {"specification": specification, "rerun": True})
            if owned:
                fresh = context.manifest()
                fresh["entries"].append({"specification": specification, **submitted})
                atomic_json(context.state / "manifest.json", fresh)
        result["new_attempt"] = submitted
        return result

    plan = Plan(title=f"Restart {name}",
                summary="Cancels this attempt and its live tiles, then submits a new attempt. Every tile "
                        "that is finished with two live copies is reused; only the rest is recomputed.",
                facts={"root": root["run_id"], "state": root["state"],
                       "children": [(child["run_id"], child["state"]) for child in children],
                       "newer": [tuple(row) for row in newer]},
                action=restart,
                items=([f"recomputed: cancelled tile {row[0]},{row[1]}" for row in cancelled] +
                       [f"cancelled now: {child['state']} {label(context, child)[1]}" for child in children]),
                confirm_text=f"{p}^{r}", reauth=True)
    if root["state"] == "complete":
        plan.blockers.append("This attempt already completed.")
    if any(state not in TERMINAL for _, state in newer):
        plan.blockers.append("A newer attempt of this field is already active.")
    if any(child["state"] == "running" for child in children):
        plan.warnings.append("Running tiles lose their unfinished work (finished tiles are kept).")
    if not owned:
        plan.warnings.append("This root was not submitted by the feeder; the new attempt is submitted "
                             "directly and the feeder will not track it.")
    return plan


# ----- feeder commands -----------------------------------------------------

def feeder_files(context: Context) -> tuple[dict, dict]:
    pipeline_path = context.state / "pipeline.json"
    if not pipeline_path.exists():
        raise CommandError("this deployment has no feeder (pipeline.json)", 404)
    return json.loads(pipeline_path.read_text()), context.manifest()


def feeder_settings(context: Context, params: dict) -> Plan:
    from campaigns.king_hamming import DEFAULTS, matching_policy, validate_settings
    pipeline, _ = feeder_files(context)
    current = dict(pipeline.get("settings", {}))
    policy = pipeline.get("policy", "legacy")
    allowed = set(DEFAULTS)
    if policy == "capacity":
        from campaigns.capacity_policy import DEFAULTS as extra
        allowed |= set(extra)
    changes = params.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise CommandError("changes must be a non-empty object of setting: integer")
    for key, value in list(changes.items()):
        if key not in allowed:
            raise CommandError(f"unknown setting: {key}")
        changes[key] = value = as_int(value, key)
        if value <= 0:
            raise CommandError(f"{key} must be a positive integer")
    proposed = {**current, **changes}
    defaults = dict(DEFAULTS)
    if policy == "capacity":
        from campaigns.capacity_policy import DEFAULTS as extra
        defaults.update(extra)
    plan = Plan(title="Change feeder limits",
                summary="The feeder reads these at its next pass (within about two minutes).",
                facts={"settings": current, "policy": policy},
                action=lambda: write_settings(context, current, changes),
                changes=[{"label": key, "before": current.get(key), "after": value}
                         for key, value in sorted(changes.items()) if current.get(key) != value],
                reauth=True)
    if not plan.changes:
        plan.blockers.append("Nothing would change.")
    try:
        # The feeder fills missing settings from its defaults before validating.
        validate_settings({**defaults, **proposed}, policy)
        matching_policy({"policy": policy, "settings": {**defaults, **proposed}})
    except ValueError as error:
        plan.blockers.append(f"The feeder would reject these limits: {error}")
    if any(proposed.get(key) != current.get(key) for key in
           ("max_visits", "max_state_bytes", "dp_threads", "tile_side")):
        added = upcoming_fields(context.state, proposed, limit=12)
        before = {tuple(item["field"]) for item in upcoming_fields(context.state, current, limit=500)}
        new = [item for item in added if tuple(item["field"]) not in before]
        if new:
            plan.items = [f"newly eligible: {ascii_field(item['field'])} (q = {item['q']:,})" for item in new]
            plan.warnings.append("The feeder adds new fields gradually, within its root and backpressure limits.")
    for key in ("max_matching_bytes", "max_tile_bytes", "max_state_bytes"):
        if proposed.get(key, 0) > current.get(key, 0):
            plan.warnings.append(f"Raising {key} lets jobs use more memory per machine.")
    return plan


def write_settings(context: Context, seen: dict, changes: dict) -> dict:
    path = context.state / "pipeline.json"
    with PipelineLock(context.state):
        pipeline = json.loads(path.read_text())
        if pipeline.get("settings") != seen:
            raise CommandError("the feeder's settings changed meanwhile; review again", 409)
        pipeline["settings"].update(changes)
        atomic_json(path, pipeline)
    return {"settings": changes}


def field_entries(manifest: dict, p: int, r: int) -> list[dict]:
    return [entry for entry in manifest.get("entries", [])
            if entry["specification"].get("program") == "dp_distributed"
            and entry["specification"]["arguments"].get("p") == p
            and entry["specification"]["arguments"].get("r") == r]


def feeder_retry(context: Context, params: dict) -> Plan:
    p, r = params.get("p"), params.get("r")
    if type(p) is not int or type(r) is not int:
        raise CommandError("p and r must be integers")
    pipeline, manifest = feeder_files(context)
    record = pipeline.get("fields", {}).get(f"{p}^{r}", {})
    entries = field_entries(manifest, p, r)
    with context.database() as connection:
        states = {row["run_id"]: dict(row) for row in connection.execute(
            f"SELECT run_id,state,error FROM runs WHERE run_id IN ({','.join('?' for _ in entries)})",
            [entry["run_id"] for entry in entries])} if entries else {}
        reusable = connection.execute(
            "SELECT COUNT(DISTINCT t.row || ',' || t.column) FROM distributed_tiles t "
            "JOIN runs c ON c.run_id=t.child_run_id WHERE c.state='complete' AND t.parent_run_id IN "
            f"({','.join('?' for _ in entries)})", [entry["run_id"] for entry in entries]
        ).fetchone()[0] if entries else 0
    attempts = [(entry["run_id"], (states.get(entry["run_id"]) or {}).get("state")) for entry in entries]
    maximum = pipeline.get("settings", {}).get("max_dp_attempts", 3)

    def retry():
        with PipelineLock(context.state):
            fresh = context.manifest()
            latest = field_entries(fresh, p, r)
            if [entry["run_id"] for entry in latest] != [run_id for run_id, _ in attempts]:
                raise CommandError("the field's attempts changed meanwhile; review again", 409)
            specification = json.loads(json.dumps(latest[-1]["specification"]))
            result = context.leader("/v1/enqueue", {"specification": specification, "rerun": True})
            fresh["entries"].append({"specification": specification, **result})
            atomic_json(context.state / "manifest.json", fresh)
        return result

    plan = Plan(title=f"Retry DP for {p}^{r}",
                summary=f"Submits attempt {len(entries) + 1}. Finished tiles from earlier attempts "
                        "that still have two live copies are reused, not recomputed.",
                facts={"attempts": attempts, "artifact": bool(record.get("dp_artifact"))},
                action=retry,
                items=[f"attempt {index + 1}: {state}" + (f" — {(states[run_id]['error'] or '')[:160]}"
                                                         if run_id in states and states[run_id]["error"] else "")
                       for index, (run_id, state) in enumerate(attempts)])
    if not entries:
        plan.blockers.append("The feeder has never submitted this field; nothing to retry.")
    if record.get("dp_artifact"):
        plan.blockers.append("This field's DP is already complete.")
    if any(state not in TERMINAL for _, state in attempts):
        plan.blockers.append("An attempt for this field is still active.")
    if reusable:
        plan.warnings.append(f"Up to {reusable} finished tiles can be reused.")
    errors = [states[run_id]["error"] for run_id, _ in attempts if run_id in states and states[run_id]["error"]]
    if errors and all("timed out" in error for error in errors):
        plan.warnings.append("Every earlier attempt failed on a tile timeout; this retry may fail the same way "
                             "unless something about those tiles has changed.")
    if len(entries) + 1 > maximum:
        plan.warnings.append(f"This exceeds the feeder's max_dp_attempts ({maximum}); if it fails, the feeder "
                             "will not retry it automatically.")
    return plan


def feeder_extend(context: Context, params: dict) -> Plan:
    from dp_solver import scheduling
    max_visits, limit = as_int(params.get("max_visits"), "max_visits"), as_int(params.get("limit"), "limit")
    if max_visits <= 0 or not 1 <= limit <= 100:
        raise CommandError("max_visits must be a positive integer and limit 1-100")
    manifest = context.manifest()
    known = {(entry["specification"]["arguments"]["p"], entry["specification"]["arguments"]["r"])
             for entry in manifest.get("entries", []) if "p" in entry["specification"].get("arguments", {})}
    with context.database() as connection:
        for (encoded,) in connection.execute("SELECT specification FROM runs WHERE parent_run_id IS NULL"):
            specification = json.loads(encoded)
            if specification.get("program") in {"dp", "dp_distributed"}:
                known.add((specification["arguments"]["p"], specification["arguments"]["r"]))
    # The same selection launch_dp.py extend makes.
    chosen = []
    try:
        for specification in scheduling.campaign(16 * 1024**3, max_visits, 4, 512):
            key = (specification["arguments"]["p"], specification["arguments"]["r"])
            if key not in known:
                chosen.append(key)
                if len(chosen) >= limit:
                    break
    except ValueError as error:
        # launch_dp.py extend uses the same function and would fail the same way.
        raise CommandError(f"The DP scheduler (dp_solver/scheduling.py campaign()) failed: {error}. "
                           "launch_dp.py extend would fail the same way.", 500) from None

    def extend():
        with PipelineLock(context.state):  # launch_dp.py rewrites manifest.json
            completed = subprocess.run(
                [context.python, str(context.launcher), "--state", str(context.state), "extend",
                 "--max-visits", str(max_visits), "--limit", str(limit)],
                capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        if completed.returncode:
            raise CommandError(f"extend failed: {(completed.stderr or completed.stdout).strip()[-500:]}", 500)
        output = completed.stdout
        start = output.find("\n{") + 1 if not output.startswith("{") else 0
        try:
            return json.loads(output[start:]) if "{" in output else {"output": output[-2000:]}
        except ValueError:
            return {"output": output[-2000:]}

    plan = Plan(title="Add fields beyond the feeder's limit",
                summary="Submits these DP roots right away, outside the feeder's gradual root cap "
                        "(launch_dp.py extend). The feeder then collects and matches them as usual.",
                facts={"fields": chosen, "max_visits": max_visits},
                action=extend,
                items=[f"{ascii_field(key)} (q = {key[0] ** key[1]:,})" for key in chosen],
                reauth=True)
    if not chosen:
        plan.blockers.append("No new field fits that visit limit.")
    plan.warnings.append("Raising the feeder's max_visits instead lets it add fields gradually.")
    return plan


TILE_SIDES = (512, 1024, 2048, 4096, 8192, 16384)
MAX_TILES = 10_000  # dp_solver/distributed.py create() default


def tile_plan(p: int, r: int, threads: int, max_tile_bytes: int, sides=TILE_SIDES,
              max_tiles: int = MAX_TILES) -> dict | None:
    """The smallest tile side the leader will accept: the same tile-count and
    per-tile memory checks as distributed.create(), so the root is not refused."""
    from dp_solver.scheduling import plan_tiles
    return plan_tiles(p, r, threads, max_tile_bytes, max_tiles, sides)


def measured_rate(context: Context, now_value: float) -> float | None:
    """Median raw visits per second of DP roots completed in the last week (1e12+ visits)."""
    import statistics
    from dp_solver.scheduling import dp_estimate
    rates = []
    with context.database() as connection:
        for encoded, created, finished in connection.execute(
                "SELECT specification,created,finished FROM runs WHERE parent_run_id IS NULL "
                "AND state='complete' AND finished>? AND specification LIKE '%dp_distributed%'",
                (now_value - 7 * 86400,)):
            arguments = json.loads(encoded)["arguments"]
            visits = dp_estimate({"arguments": arguments})["raw_visits"]
            if visits >= 1e12 and finished > created:
                rates.append(visits / (finished - created))
    return statistics.median(rates) if rates else None


DISK_FLOOR_DEFAULT = 10 * 1024**3
STORAGE_MARGIN = 1.25          # edge bands and cells compress a little differently from field to field
FALLBACK_BYTES_PER_CELL = 1.0


def storage_per_cell(connection, tile_format: int = 1) -> float:
    """Bytes of stored tile packet per DP cell, from the fields of this tile format finished so far
    (their sizes survive tile deletion). With none finished yet, format 2 uses the planning figure."""
    from dp_solver.scheduling import STORED_BYTES_PER_CELL, dp_estimate
    stored = cells = 0
    for encoded, size in connection.execute(
            "SELECT p.specification,SUM(a.size) FROM runs p JOIN runs c ON c.parent_run_id=p.run_id "
            "JOIN artifacts a ON a.artifact_hash=c.artifact_hash WHERE p.parent_run_id IS NULL "
            "AND p.state='complete' AND json_extract(p.specification,'$.program')='dp_distributed' "
            "AND COALESCE(json_extract(p.specification,'$.arguments.tile_format'),1)=? "
            "AND a.size IS NOT NULL GROUP BY p.run_id", (tile_format,)):
        try:
            cells += dp_estimate({"arguments": json.loads(encoded)["arguments"]})["state_bytes"] // 12
        except (ValueError, KeyError):
            continue
        stored += size or 0
    if cells:
        return stored / cells
    return FALLBACK_BYTES_PER_CELL if tile_format == 1 else STORED_BYTES_PER_CELL[tile_format]


def usable_disk(context: Context, connection, now: float, floor: int) -> tuple[int, str] | None:
    """Free bytes above each machine's floor, and where the figure came from; None when nothing is known."""
    monitor = getattr(context.snapshots, "disk", None)
    if monitor is not None:
        view = monitor.view()
        hosts = [item for item in view["hosts"].values() if item.get("size")]
        if hosts and view["measured_at"]:
            return (sum(max(0, item["free"] - floor) for item in hosts),
                    f"measured {int((now - view['measured_at']) / 60)} min ago")
    lease = float(connection.execute("SELECT value FROM settings WHERE key='lease_seconds'").fetchone()[0])
    live = [row[0] for row in connection.execute(
        "SELECT storage_free_bytes FROM nodes WHERE last_heartbeat>? AND storage_free_bytes>=0", (now - lease,))]
    return (sum(max(0, free - floor) for free in live), "reported by the leader's live machines") if live else None


def check_disk(context: Context, plan: Plan, estimate: dict, now: float, tile_format: int = 1) -> None:
    """Add the field's projected tile storage, and how it compares with the free disk, to plan."""
    from dp_solver.scheduling import dp_estimate
    gib = 1024**3
    copies = 3
    with context.database() as connection:
        rates = {fmt: storage_per_cell(connection, fmt) for fmt in (1, 2)}
        per_cell = rates[tile_format]
        floor = int(float((connection.execute("SELECT value FROM settings WHERE key='disk_floor_bytes'").fetchone()
                           or [DISK_FLOOR_DEFAULT])[0]))
        reserved = 0.0
        for encoded, done, total in connection.execute(
                "SELECT specification,progress_done,progress_total FROM runs WHERE parent_run_id IS NULL "
                "AND state IN ('waiting','queued','running','paused','stopping') "
                "AND json_extract(specification,'$.program')='dp_distributed'"):
            try:
                arguments = json.loads(encoded)["arguments"]
                cells = dp_estimate({"arguments": arguments})["state_bytes"] // 12
                rate = rates[arguments.get("tile_format", 1)]
            except (ValueError, KeyError):
                continue
            left = 1.0 - (min(done or 0, total) / total if total else 0.0)
            reserved += cells * rate * STORAGE_MARGIN * copies * left
        known = usable_disk(context, connection, now, floor)
    one_copy = estimate["state_bytes"] // 12 * per_cell * STORAGE_MARGIN
    need = one_copy * copies
    plan.changes.append({"label": "Tile storage", "before": None, "after": (
        f"≈ {need / gib:,.0f} GiB at {copies} copies ({one_copy / gib:,.0f} GiB each, "
        f"{per_cell:.2f} bytes per DP cell as measured on finished fields)")})
    if known is None:
        plan.warnings.append("Free disk is not known right now (no machine is reporting and the dashboard has not "
                             "measured the disks), so the field's storage could not be checked.")
        return
    free, source = known
    available = max(0.0, free - reserved)
    plan.changes.append({"label": "Free disk", "before": None, "after": (
        f"{free / gib:,.0f} GiB above each machine's {floor / gib:g} GiB floor ({source}); "
        f"{reserved / gib:,.0f} GiB of it is still needed by fields in progress")})
    if need > available:
        plan.blockers.append(
            f"It needs about {need / gib:,.0f} GiB of tile storage but only about {available / gib:,.0f} GiB is free "
            "after fields already in progress. Finished fields' tiles are deleted automatically (CAMPAIGN_NOTES item "
            "22), so this may fit once more fields finish, or free space first.")
    elif need > 0.5 * available:
        plan.warnings.append(f"It would use about {100 * need / available:.0f}% of the disk space that is free "
                             f"({available / gib:,.0f} GiB after fields in progress).")


def submit_field(context: Context, params: dict) -> Plan:
    """Submit a DP root for any supported field, outside the feeder's frontier."""
    import time as time_module
    from dp_solver.scheduling import dp_estimate
    p, r = as_int(params.get("p"), "p"), as_int(params.get("r"), "r")
    priority = as_int(params.get("priority", 0), "priority")
    if not PRIORITY_RANGE[0] <= priority <= PRIORITY_RANGE[1]:
        raise CommandError(f"priority must be from {PRIORITY_RANGE[0]} to {PRIORITY_RANGE[1]}")
    try:
        estimate = dp_estimate({"arguments": {"p": p, "r": r}})
    except ValueError as error:
        raise CommandError(f"{p}^{r} is not a supported field: {error}") from None
    pipeline, manifest = feeder_files(context)
    settings = pipeline.get("settings", {})
    threads = settings.get("dp_threads", 16)
    max_tile_bytes = settings.get("max_tile_bytes", 2 * 1024**3)
    max_tiles = settings.get("max_tiles", MAX_TILES)
    tile_format = settings.get("tile_format", 1)
    side = params.get("tile_side")
    plan_tiles = tile_plan(p, r, threads, max_tile_bytes,
                           (as_int(side, "tile_side"),) if side not in (None, "") else TILE_SIDES, max_tiles)
    existing = [entry for entry in manifest.get("entries", [])
                if entry["specification"].get("arguments", {}).get("p") == p
                and entry["specification"]["arguments"].get("r") == r]
    with context.database() as connection:
        roots = [tuple(row) for row in connection.execute(
            "SELECT run_id,state FROM runs WHERE parent_run_id IS NULL AND specification LIKE ? "
            "AND specification LIKE ? AND specification LIKE '%\"program\":\"dp%'",
            (f'%"p":{p},%', f'%"r":{r},%'))]
        largest = 0
        for (encoded,) in connection.execute(
                "SELECT specification FROM runs WHERE parent_run_id IS NULL AND state='complete' "
                "AND specification LIKE '%dp_distributed%'"):
            largest = max(largest, dp_estimate({"arguments": json.loads(encoded)["arguments"]})["state_bytes"])
    specification = {"program": "dp_distributed", "arguments": {
        "p": p, "r": r, "threads": threads, "tile_side": plan_tiles["side"] if plan_tiles else 512,
        "max_state_bytes": max(settings.get("max_state_bytes", 16 * 1024**3), estimate["state_bytes"]),
        "max_visits": max(settings.get("max_visits", 30_000_000_000_000), estimate["raw_visits"]),
        "max_tile_bytes": plan_tiles["reserve"] if plan_tiles else max_tile_bytes, "artifact_format": "KHD1"}}
    if max_tiles != MAX_TILES:
        specification["arguments"]["max_tiles"] = max_tiles
    if tile_format != 1:
        specification["arguments"]["tile_format"] = tile_format

    def submit():
        with PipelineLock(context.state):
            fresh = context.manifest()
            if any(entry["specification"].get("arguments", {}).get("p") == p and
                   entry["specification"]["arguments"].get("r") == r for entry in fresh.get("entries", [])):
                raise CommandError(f"{p}^{r} was submitted meanwhile; review again", 409)
            result = context.leader("/v1/enqueue", {"specification": specification, "priority": priority})
            fresh["entries"].append({"specification": specification, **result})
            atomic_json(context.state / "manifest.json", fresh)
        return result

    gib = 1024**3
    big = (estimate["raw_visits"] > settings.get("max_visits", 0) or
           estimate["state_bytes"] > settings.get("max_state_bytes", 0))
    plan = Plan(
        title=f"Submit DP for {p}^{r}",
        summary="Queues a new distributed DP root and records it in the feeder's manifest, so the feeder "
                "collects the result and submits matching when the limits allow.",
        facts={"field": [p, r], "existing": [entry.get("run_id") for entry in existing], "roots": roots,
               "specification": specification, "priority": priority},
        action=submit,
        changes=[{"label": "Field size q", "before": None, "after": f"{estimate['q']:,}"},
                 {"label": "DP work", "before": None, "after": f"{estimate['raw_visits']:.3g} raw visits"},
                 {"label": "DP state", "before": None, "after": f"{estimate['state_bytes'] / gib:,.1f} GiB"},
                 {"label": "Tiles", "before": None, "after": (
                     f"{plan_tiles['per_side']} × {plan_tiles['per_side']} = {plan_tiles['tiles']:,} of side "
                     f"{plan_tiles['side']} (≤ {plan_tiles['tile_bytes'] / 1024**2:,.0f} MiB each)")
                     if plan_tiles else "no layout fits"},
                 {"label": "Priority", "before": None, "after": priority}],
        confirm_text=f"{p}^{r}" if big else None,
        reauth=True)
    if existing or roots:
        states = ", ".join(sorted({state for _, state in roots})) or "submitted"
        plan.blockers.append(f"{p}^{r} already exists ({states}). Use Retry on the Feeder tab or "
                             "Restart field on the DP tiles tab instead.")
    if not plan_tiles:
        plan.blockers.append(f"No tile side up to {TILE_SIDES[-1] if side in (None, '') else side} keeps both "
                             f"the tile count ≤ {max_tiles:,} and each tile within max_tile_bytes "
                             f"({max_tile_bytes / 1024**3:g} GiB). Large primes have large tile halos; raising "
                             "max_tile_bytes (or, for very large budgets, max_tiles) in the feeder limits "
                             "would allow a layout. See docs/DP_STORAGE.md.")
    rate = measured_rate(context, time_module.time())
    if rate:
        plan.warnings.append(f"Recent large roots ran at about {rate:.2g} visits/s, which suggests "
                             f"roughly {estimate['raw_visits'] / rate / 3600:,.0f} h of DP "
                             "(per-tile overhead adds more for many tiles).")
    if largest and estimate["state_bytes"] > 2 * largest:
        plan.warnings.append(
            f"Its DP state is {estimate['state_bytes'] / largest:,.0f}× the largest completed so far "
            f"({largest / gib:,.1f} GiB) before compression; the tile storage line below gives "
            "the projected worker disk.")
    check_disk(context, plan, estimate, time_module.time(), tile_format)
    if big:
        plan.warnings.append("It is beyond the feeder's own limits (max_visits / max_state_bytes); the feeder "
                             "would never have chosen it, but will still collect its result.")
    if estimate["q"] > settings.get("max_field_elements", 100_000_000):
        plan.warnings.append(f"q = {estimate['q']:,} exceeds the matching field limit "
                             f"({settings.get('max_field_elements', 100_000_000):,}), so it will not be "
                             "matched automatically under the current limits.")
    return plan


# ----- process jobs --------------------------------------------------------

def busy_job(context: Context, plan: Plan) -> None:
    running = context.jobs.running()
    if running:
        plan.blockers.append(f"Another job is running: {running['title']}.")
        plan.facts = {**plan.facts, "job": running["id"]}


def active_runs(context: Context) -> list[str]:
    with context.database() as connection:
        return [row[0] for row in connection.execute(
            "SELECT run_id FROM runs WHERE state IN ('running','stopping') ORDER BY run_id")]


def launcher_job(context: Context, name: str, title: str, action: str) -> Callable[[], dict]:
    argv = [context.python, str(context.launcher), "--state", str(context.state), action]
    return lambda user: context.jobs.start(name, title, argv, user, cwd=ROOT)


def ensure_feeder(context: Context, params: dict) -> Plan:
    process = feeder_process(context.state)
    plan = Plan(title="Start the feeder",
                summary="Starts the exact recorded feeder command (launch_dp.py ensure-feeder).",
                facts={"alive": process.get("alive"), "pid": process.get("pid")},
                action=launcher_job(context, "ensure-feeder", "Start the feeder", "ensure-feeder"),
                reauth=True, job=True)
    if not process.get("recorded"):
        plan.blockers.append("No feeder_process.json is recorded for this deployment.")
    elif process.get("alive"):
        plan.blockers.append("The feeder is already running.")
    busy_job(context, plan)
    return plan


RESTART_FEEDER = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                  "from dp_solver.launch_dp import restart_owned_feeder; "
                  "print('restarted' if restart_owned_feeder(Path(sys.argv[2])) else 'no feeder configured')")


def restart_feeder(context: Context, params: dict) -> Plan:
    process = feeder_process(context.state)
    git = git_state(ROOT)
    argv = [context.python, "-c", RESTART_FEEDER, str(ROOT), str(context.state)]
    plan = Plan(title="Restart the feeder",
                summary="Stops the recorded feeder and starts the same command, so it loads the "
                        "current feeder code. Its retained state is untouched.",
                facts={"alive": process.get("alive"), "pid": process.get("pid"), "git": git},
                action=lambda user: context.jobs.start("restart-feeder", "Restart the feeder", argv, user,
                                                       cwd=ROOT),
                reauth=True, job=True)
    if not process.get("alive"):
        plan.blockers.append("The feeder is not running; use Start the feeder.")
    if git.get("dirty"):
        plan.warnings.append(f"It will load your working tree, which has {len(git['dirty'])} uncommitted "
                             "change(s).")
        plan.items = [f"modified: {path}" for path in git["dirty"][:20]]
    busy_job(context, plan)
    return plan


def upgrade(context: Context, params: dict, which: str) -> Plan:
    git = git_state(ROOT)
    active = active_runs(context)
    with context.database() as connection:
        dispatch_state = connection.execute(
            "SELECT value FROM settings WHERE key='campaign_state'").fetchone()[0]
    title = "Upgrade workers" if which == "workers" else "Upgrade the leader"
    plan = Plan(
        title=title,
        summary=("Rebuilds the solvers, replaces every worker's runtime with your working tree, keeps all "
                 "retained data, and restarts the feeder (launch_dp.py upgrade-workers). Dispatch is left "
                 "stopped; resume it afterwards." if which == "workers" else
                 "Restarts the leader from your working tree, keeping worker sessions "
                 "(launch_dp.py upgrade-leader). Dispatch is left stopped; resume it afterwards."),
        facts={"active": active, "git": git, "dispatch": dispatch_state},
        action=launcher_job(context, f"upgrade-{which}", title, f"upgrade-{which}"),
        confirm_text=f"upgrade {which}" if which == "workers" else "upgrade leader",
        reauth=True, job=True)
    if active:
        plan.blockers.append(f"{len(active)} run(s) are active. Stop dispatch and wait until nothing is "
                             "running; the upgrade refuses otherwise.")
    if git.get("dirty"):
        plan.warnings.append(f"Your working tree has {len(git['dirty'])} uncommitted change(s); they will be "
                             "deployed as they are.")
        plan.items = [f"modified: {path}" for path in git["dirty"][:20]]
    plan.changes = [{"label": "Code", "before": "deployed bundle",
                     "after": f"working tree at {git.get('head', '?')}{' + changes' if git.get('dirty') else ''}"}]
    rollout = context.state / "last_rollout.json"
    if which == "workers" and rollout.exists():
        try:
            stage = json.loads(rollout.read_text()).get("stage")
            plan.warnings.append(f"The previous rollout ended at stage “{stage}”.")
        except ValueError:
            pass
    busy_job(context, plan)
    return plan


def cancel_job(context: Context, params: dict) -> Plan:
    job_id = params.get("job_id")
    record = context.jobs.record(job_id) if isinstance(job_id, str) and job_id.replace("-", "").isalnum() else None
    if record is None:
        raise CommandError("no such job", 404)
    upgrade = record["name"].startswith("upgrade-")
    plan = Plan(title=f"Stop job: {record['title']}",
                summary="Sends the job's processes a stop signal, and force-stops them after 15 seconds if "
                        "they are still running. Services the job already started (feeder, leader, agents) "
                        "keep running.",
                facts={"job": job_id, "status": record["status"], "child": record.get("child_pid")},
                action=lambda: context.jobs.cancel(job_id),
                confirm_text="stop job" if upgrade else None, reauth=True)
    if record["status"] != "running":
        plan.blockers.append(f"This job is already {record['status']}.")
    elif not record.get("child_pid"):
        plan.blockers.append("The job's command has not started yet; try again in a moment.")
    if upgrade:
        plan.warnings.append("Stopping an upgrade partway can leave some workers on the new runtime and some on "
                             "the old. last_rollout.json records the stage reached and how to recover; dispatch "
                             "stays stopped until you resume it.")
    return plan


COMMANDS: dict[str, Callable[[Context, dict], Plan]] = {
    "dispatch.stop": lambda c, p: dispatch(c, p, "stopped"),
    "dispatch.resume": lambda c, p: dispatch(c, p, "running"),
    "run.pause": lambda c, p: run_control(c, p, "pause"),
    "run.resume": lambda c, p: run_control(c, p, "resume"),
    "run.cancel": lambda c, p: run_control(c, p, "cancel"),
    "run.priority": run_priority,
    "root.cancel_leftovers": cancel_orphans,
    "root.restart": restart_root,
    "feeder.settings": feeder_settings,
    "feeder.retry": feeder_retry,
    "feeder.extend": feeder_extend,
    "field.submit": submit_field,
    "process.ensure_feeder": ensure_feeder,
    "process.restart_feeder": restart_feeder,
    "process.upgrade_workers": lambda c, p: upgrade(c, p, "workers"),
    "process.upgrade_leader": lambda c, p: upgrade(c, p, "leader"),
    "process.cancel_job": cancel_job,
}


class CommandService:
    """Serialize command execution and keep the audit trail."""

    def __init__(self, context: Context, audit) -> None:
        self.context = context
        self.audit = audit
        self.lock = threading.Lock()

    def plan(self, name: str, params) -> Plan:
        if name not in COMMANDS:
            raise CommandError(f"unknown command: {name}", 404)
        if not isinstance(params, dict):
            raise CommandError("params must be an object")
        return COMMANDS[name](self.context, params)

    def preview(self, name: str, params) -> dict:
        return self.plan(name, params).describe(name)

    def run(self, name: str, params, fingerprint: str, confirm: str | None, session: dict,
            address: str, recently_authenticated: bool) -> dict:
        with self.lock:
            plan = self.plan(name, params)
            if plan.fingerprint != fingerprint:
                raise CommandError("Things changed since this preview. Review the new one.", 409,
                                   preview=plan.describe(name))
            if plan.blockers:
                raise CommandError(plan.blockers[0], 409, preview=plan.describe(name))
            if plan.confirm_text and (confirm or "").strip() != plan.confirm_text:
                raise CommandError(f"Type “{plan.confirm_text}” to confirm.", 400)
            if plan.reauth and not recently_authenticated:
                raise CommandError("Confirm your password first.", 403, reauth_required=True)
            try:
                result = plan.action(session["user"]) if plan.job else plan.action()
            except CommandError as error:
                self.audit.record("command", session["user"], address, "failed", command=name,
                                  params=params, error=error.message)
                raise
            except Exception as error:
                self.audit.record("command", session["user"], address, "failed", command=name,
                                  params=params, error=f"{type(error).__name__}: {error}")
                raise CommandError(f"{type(error).__name__}: {error}", 500) from None
            self.audit.record("command", session["user"], address, "ok", command=name, params=params,
                              title=plan.title, result=result)
            self.context.snapshots.invalidate()
            return {"ok": True, "title": plan.title, "result": result, "job": result.get("id") if plan.job else None}
