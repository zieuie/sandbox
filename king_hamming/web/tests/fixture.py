"""Build small, self-contained campaign deployments for dashboard tests."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
for path in (WEB, ROOT, ROOT / "cluster"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import leader  # noqa: E402

NOW = 1_800_000_000.0
SIDE = 2  # 5^3 has DP budget 25, so a side-2 grid has plenty of valid tiles


def database(directory: Path) -> sqlite3.Connection:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "leader.sqlite"
    leader.initialize(path, 1800)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def node(connection, name, address, cpu_set, heartbeat, memory=16 * 1024**3):
    connection.execute(
        "INSERT INTO nodes(node_name,address,cpu_set,storage_root,last_heartbeat,state,"
        "memory_bytes,physical_core_count,slots_json) VALUES(?,?,?,?,?,'healthy',?,?,'[]')",
        (name, address, cpu_set, "/tmp", heartbeat, memory, 4))


def run(connection, run_id, specification, state, parent=None, **columns):
    values = {"run_id": run_id, "calculation_id": hashlib.sha256(run_id.encode()).hexdigest(),
              "specification": json.dumps(specification, sort_keys=True, separators=(",", ":")),
              "state": state, "priority": 0, "from_scratch": 0, "created": NOW - 7200,
              "parent_run_id": parent, **columns}
    names = ",".join(values)
    connection.execute(f"INSERT INTO runs({names}) VALUES({','.join('?' for _ in values)})",
                       tuple(values.values()))


def artifact(connection, digest, nodes):
    connection.execute("INSERT INTO artifacts(artifact_hash,target_replicas,created) VALUES(?,2,?)",
                       (digest, NOW))
    for name in nodes:
        connection.execute("INSERT INTO replicas(artifact_hash,node_name,location,created) "
                           "VALUES(?,?,'x',?)", (digest, name, NOW))


def tile_spec(row, column, parent):
    return {"program": "dp_tile", "arguments": {"p": 5, "r": 3, "row": row, "column": column,
                                                "parent_run_id": parent, "tile_side": SIDE}}


def live_campaign(deployments: Path) -> None:
    """One live DP root on a 2x3 grid, two healthy nodes and one stale node.

    (0,0) durable, (0,1) ready, (0,2) complete with one live copy,
    (1,0) running on alpha, (1,1) blocked on (0,1).
    """
    connection = database(deployments / "live")
    node(connection, "dp-101", "http://192.168.4.101:9000", "0,1,2,3", NOW - 5)
    node(connection, "dp-151", "http://192.168.4.151:9000", "1,2,3,5,6,7", NOW - 5, 40 * 1024**3)
    node(connection, "dp-108", "http://192.168.4.108:9000", "0,1,2,3", NOW - 3600)
    root = "root-5-3"
    run(connection, root, {"program": "dp_distributed",
                           "arguments": {"p": 5, "r": 3, "tile_side": SIDE}}, "waiting",
        started=NOW - 7000)
    for row, column, child, state, extra in (
            (0, 0, "t00", "complete", {"artifact_hash": "a" * 64, "started": NOW - 6000, "finished": NOW - 5400}),
            (0, 2, "t02", "complete", {"artifact_hash": "b" * 64, "started": NOW - 5000, "finished": NOW - 4700}),
            (1, 0, "t10", "running", {"node_name": "dp-101", "assigned_cpu_set": "0,1",
                                      "exclusive_host": 0, "reserved_memory_bytes": 2 * 1024**3,
                                      "started": NOW - 600, "last_solver_heartbeat": NOW - 2,
                                      "last_progress_at": NOW - 2, "progress_done": 25,
                                      "progress_total": 100, "progress_phase": "computing",
                                      "progress_units": "cells", "lease_token": "lease-t10"})):
        run(connection, child, tile_spec(row, column, root), state, parent=root, **extra)
    for row, column, child in ((0, 0, "t00"), (0, 1, None), (0, 2, "t02"), (1, 0, "t10"), (1, 1, None)):
        connection.execute("INSERT INTO distributed_tiles(parent_run_id,row,column,child_run_id) "
                           "VALUES(?,?,?,?)", (root, row, column, child))
    artifact(connection, "a" * 64, ["dp-101", "dp-151"])
    artifact(connection, "b" * 64, ["dp-101", "dp-108"])  # dp-108 is stale

    # 120 CPU-seconds across 60 s on a 4-CPU node: half the node for one minute.
    connection.execute("INSERT INTO lease_history(lease_token,run_id,node_name,attempt,started) "
                       "VALUES('lease-t10','t10','dp-101',1,?)", (NOW - 3600,))
    for cpu, recorded in ((10_000_000, NOW - 120), (130_000_000, NOW - 60)):
        connection.execute(
            "INSERT INTO resource_usage_samples(run_id,lease_token,node_name,component,shard_index,"
            "cpu_microseconds,rss_bytes,recorded) VALUES('t10','lease-t10','dp-101','solver',0,?,0,?)",
            (cpu, recorded))
    connection.commit()
    connection.close()


def results_campaign(deployments: Path) -> dict:
    """An offline deployment with collected DP, a missing DP and a matched field."""
    directory = deployments / "older"
    connection = database(directory)
    node(connection, "dp-101", "http://192.168.4.101:9000", "0,1", NOW - 86400)
    (directory / "results").mkdir()
    (directory / "matching-results").mkdir()

    raw = (ROOT / "examples" / "5_3.khdp").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    run(connection, "dp53", {"program": "dp_distributed", "arguments": {"p": 5, "r": 3, "tile_side": 8}},
        "complete", artifact_hash=digest, finished=NOW - 100_000)
    (directory / "results" / "5_3_dp53.khdp").write_bytes(raw)
    run(connection, "dp33", {"program": "dp_distributed", "arguments": {"p": 3, "r": 3, "tile_side": 8}},
        "complete", artifact_hash="c" * 64, finished=NOW - 100_000)  # never collected
    run(connection, "dp75", {"program": "dp_distributed", "arguments": {"p": 7, "r": 5, "tile_side": 8}},
        "failed", error="tile 1,1: timed out", finished=NOW - 100_000)

    dp_raw = (ROOT / "matching_solver" / "examples" / "13_5.khdp").read_bytes()
    certificate = ROOT / "matching_solver" / "examples" / "13_5.khmatch"
    certificate_hash = hashlib.sha256(certificate.read_bytes()).hexdigest()
    run(connection, "m135", {"program": "match", "arguments": {
        "dp_b64": base64.b64encode(dp_raw).decode(), "dp_sha256": hashlib.sha256(dp_raw).hexdigest(),
        "poly": [2, 4, 0, 0, 0, 1]}}, "complete", artifact_hash=certificate_hash, finished=NOW - 90_000)
    shutil.copy(certificate, directory / "matching-results" / "13_5_m135.khmatch")
    connection.commit()
    connection.close()

    (directory / "pipeline.json").write_text(json.dumps({"fields": {
        "5^3": {"p": 5, "r": 3, "matching_admission": "field limit"}}}))
    return {"dp_digest": digest}


def feeder_state(deployments: Path) -> None:
    """Pipeline files for the live campaign: one given-up field, a dead feeder pid,
    and leader/feeder logs with one earlier session."""
    state = deployments / "live"
    (state / "pipeline.json").write_text(json.dumps({
        "version": 1, "policy": "legacy", "feeder_state": "running", "last_reconcile": NOW - 60,
        "demand": {"ready_dp_tiles": 1, "ready_tile_target": 18, "active_dp_roots": 1},
        "settings": {"max_dp_attempts": 3, "max_dp_roots": 8, "target_dp_roots": 2,
                     "max_ready_fields": 4, "minimum_free_bytes": 1, "max_state_bytes": 16 * 1024**3,
                     "max_visits": 30_000_000_000_000, "dp_threads": 16, "tile_side": 512},
        "fields": {
            "5^3": {"p": 5, "r": 3, "dp_attempts": [{"run_id": "root-5-3", "state": "waiting"}],
                    "dp_current_run_id": "root-5-3", "dp_current_state": "waiting",
                    "dp_state": "waiting", "matching_attempts": []},
            "97^3": {"p": 97, "r": 3, "dp_attempts": [{"run_id": f"x{i}", "state": "failed"} for i in range(3)],
                     "dp_current_run_id": "x2", "dp_current_state": "failed", "dp_state": "failed",
                     "matching_attempts": []},
        }}))
    (state / "feeder_process.json").write_text(json.dumps({
        "pid": 2**22 + 12345, "command": ["python3", "continuous_campaign.py", "--state", str(state),
                                          "run", "--interval", "120"]}))
    (state / "leader.log").write_text(
        "leader listening on http://0.0.0.0:8061\n"
        "Exception occurred during processing of request from ('192.168.4.101', 5000)\n"
        "Traceback (most recent call last):\n"
        "sqlite3.OperationalError: database is locked\n"
        + "-" * 40 + "\n"
        "leader listening on http://0.0.0.0:8061\n"
        "Exception occurred during processing of request from ('192.168.4.151', 5001)\n"
        "Traceback (most recent call last):\n"
        "BrokenPipeError: [Errno 32] Broken pipe\n"
        + "-" * 40 + "\n")
    (state / "feeder.log").write_text(
        '{"collected_dp": 0, "added_dp": 1, "matching": {}}\n'
        "continuous campaign will retry: <urlopen error [Errno 101] Network is unreachable>\n"
        '{"collected_dp": 0, "added_dp": 0, "matching": {}}\n')


LOCKED_BLOCK = ("Exception occurred during processing of request from ('192.168.4.101', 6000)\n"
                "Traceback (most recent call last):\n"
                "  File \"leader.py\", line 508, in dispatch_post\n"
                "sqlite3.OperationalError: database is locked\n" + "-" * 40 + "\n")


class Client:
    """Minimal HTTP client that keeps the session cookie and CSRF token."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.cookie = None
        self.csrf = None

    def request(self, path, method="GET", body=None, headers=None, csrf=True):
        import http.client
        from urllib.parse import urlparse
        url = urlparse(self.base)
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=30)
        sent = {"Origin": self.base} if method == "POST" else {}
        if body is not None:
            sent["Content-Type"] = "application/json"
        if self.cookie:
            sent["Cookie"] = self.cookie
        if csrf and self.csrf and method == "POST":
            sent["X-CSRF-Token"] = self.csrf
        sent.update(headers or {})
        payload = None if body is None else json.dumps(body).encode()
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def json(self, path, method="GET", body=None, **options):
        status, headers, data = self.request(path, method, body, **options)
        try:
            return status, headers, json.loads(data) if data else None
        except ValueError:
            return status, headers, None

    def login(self, user, password):
        status, headers, value = self.json("/api/login", "POST", {"user": user, "password": password})
        if status == 200:
            self.cookie = headers["Set-Cookie"].split(";")[0]
            self.csrf = value["csrf"]
        return status, headers, value


PASSWORD = "correct horse battery"


def serve(deployments: Path, state: Path, launcher: Path | None = None, **options):
    """Start a dashboard server in a thread; return (httpd, base URL, auth)."""
    import threading
    from http.server import ThreadingHTTPServer
    from audit import Audit
    from auth import Auth
    from commands import CommandService, Context
    from jobs import Jobs
    import server
    import snapshot
    auth = Auth(state)
    auth.add_user("tester", PASSWORD, "operator")
    auth.add_user("guest", PASSWORD + "!", "viewer")
    snapshots = snapshot.Snapshots(deployments, "live")
    audit = Audit(state)
    context = Context(deployments, "live", snapshots, Jobs(state / "jobs"))
    if launcher is not None:
        context.launcher = launcher
    config = server.Config(snapshots, auth, audit, CommandService(context, audit), **options)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(config))
    httpd.quiet = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}", auth


class FakeLeader:
    """Record leader API calls and answer like the real leader would."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        calls = self.calls = []
        self.refuse = set()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
                calls.append((handler.path, body))
                if handler.path in self.refuse:
                    reply, status = {"error": "terminal runs cannot be controlled"}, 400
                elif handler.path == "/v1/enqueue":
                    reply, status = {"run_id": f"new-{len(calls)}", "state": "waiting", "reused": False}, 200
                elif handler.path == "/v1/control":
                    reply, status = {"campaign_state": body["state"]}, 200
                else:
                    reply, status = {"run_id": body.get("run_id"), "state": "cancelled"}, 200
                data = json.dumps(reply).encode()
                handler.send_response(status)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)

            def log_message(handler, *arguments):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def command_campaign(deployments: Path, leader_url: str) -> None:
    """Add a manifest, three failed 97^3 attempts and a pipeline lock to the live fixture."""
    state = deployments / "live"
    connection = sqlite3.connect(state / "leader.sqlite")
    entries = [{"specification": {"program": "dp_distributed",
                                  "arguments": {"p": 5, "r": 3, "tile_side": SIDE}},
                "run_id": "root-5-3", "state": "waiting"}]
    for index in range(3):
        specification = {"program": "dp_distributed", "arguments": {"p": 97, "r": 3, "tile_side": 512}}
        run(connection, f"x{index}", specification, "failed", error=f"tile {index},1: timed out",
            finished=NOW - 1000 + index, created=NOW - 5000 + index)
        entries.append({"specification": specification, "run_id": f"x{index}", "state": "failed"})
    connection.commit()
    connection.close()
    (state / "manifest.json").write_text(json.dumps({"leader": leader_url, "entries": entries}))
    (state / "pipeline.lock").touch()


def fake_launcher(directory: Path, delay: float = 0.0) -> Path:
    """A stand-in for launch_dp.py that echoes its arguments (and extend's JSON)."""
    path = directory / "fake_launch.py"
    path.write_text(
        "import json, sys, time\n"
        f"time.sleep({delay})\n"
        "print('fake launcher:', ' '.join(sys.argv[1:]))\n"
        "if 'extend' in sys.argv: print(json.dumps({'added_calculations': 0}))\n"
        "sys.exit(3 if 'upgrade-leader' in sys.argv else 0)\n")
    return path
