#!/usr/bin/env python3
"""Inject transfer failures, stale leases, and whole-worker loss into checkpoint recovery."""

from __future__ import annotations

import copy
import hashlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

os.environ["KH_ENABLE_TEST_FIXTURES"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent
import checkpoints
import leader
from blob_store import blob_path, fetch_blob, store_blob
from common import calculation_id
from test_integration import find_run, free_port, request_json, wait_until


# Run private local processes with file-backed diagnostics and reliable cleanup.
class Cluster:
    """Own an isolated leader and any test agents; no household hosts are contacted."""

    def __init__(self, root: Path, checkpoint_seconds: int = 0, lease_seconds: float = 1.2) -> None:
        """Start a leader using root, checkpoint_seconds policy and lease_seconds renewal deadline."""

        self.root = root
        self.processes: list[subprocess.Popen[bytes]] = []
        self.logs: list[Any] = []
        self.database = root / "leader.sqlite"
        self.url = f"http://127.0.0.1:{free_port()}"
        self.start("leader", [str(ROOT / "leader.py"), "serve", "--database", str(self.database),
                              "--listen", self.url.removeprefix("http://"),
                              "--lease-seconds", str(lease_seconds), "--checkpoint-seconds", str(checkpoint_seconds)])

        def ready() -> bool:
            """Return true when the leader's health endpoint accepts connections."""

            try:
                return request_json(self.url, "GET", "/v1/health")["ok"]
            except OSError:
                return False

        wait_until(ready, "recovery leader startup")

    def start(self, name: str, arguments: list[str]) -> subprocess.Popen[bytes]:
        """Launch arguments as Python under a named log; return the owned process."""

        log = (self.root / f"{name}.log").open("ab")
        self.logs.append(log)
        process = subprocess.Popen([sys.executable, *arguments], stdout=log, stderr=log)
        self.processes.append(process)
        return process

    def worker(self, name: str, cpu_count: int, slots: int = 1) -> subprocess.Popen[bytes]:
        """Start name with cpu_count allowed CPUs and private mutable/storage roots."""

        address = f"127.0.0.1:{free_port()}"
        cpus = sorted(os.sched_getaffinity(0))[:cpu_count]
        return self.start(name, [str(ROOT / "agent.py"), "run", "--leader", self.url,
                                "--name", name, "--cpus", ",".join(map(str, cpus)),
                                "--slots", str(slots),
                                "--work-root", str(self.root / name / "work"),
                                "--storage-root", str(self.root / name / "blobs"),
                                "--storage-listen", address, "--storage-url", f"http://{address}",
                                "--poll-seconds", "0.03", "--control-seconds", "0.03"])

    def close(self) -> None:
        """Terminate every owned child and close its diagnostic file."""

        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()

        for process in reversed(self.processes):
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        for log in self.logs:
            log.close()


# Check streaming retries without allowing an incomplete or corrupt blob to become visible.
class TransferTests(unittest.TestCase):
    """Exercise real HTTP range downloads and content validation."""

    def test_interrupted_transfer_resumes_and_hashes(self) -> None:
        """Retain an interrupted prefix and request only the remaining suffix on retry."""

        payload = bytes(range(251)) * 10000
        digest = hashlib.sha256(payload).hexdigest()
        requested: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            """Cut off the first response, then honor the recorded Range header."""

            def do_GET(self) -> None:
                """Serve the test payload, deliberately truncating its first download."""

                requested.append(self.headers.get("Range"))
                offset = int(self.headers["Range"][6:-1]) if self.headers.get("Range") else 0
                self.send_response(206 if offset else 200)
                self.send_header("Content-Length", str(len(payload) - offset))

                if offset:
                    self.send_header("Content-Range", f"bytes {offset}-{len(payload)-1}/{len(payload)}")

                self.end_headers()
                self.wfile.write(payload[offset:] if len(requested) > 1 else payload[:1024 * 1024])
                self.close_connection = True

            def log_message(self, format_string: str, *arguments: object) -> None:
                """Suppress expected test HTTP access messages."""

                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                location = f"http://127.0.0.1:{server.server_port}/blob"

                with self.assertRaises(OSError):
                    fetch_blob(root, digest, len(payload), [location])

                self.assertFalse(blob_path(root, digest).exists())
                partial = root / ".downloads" / f"{digest}.part"
                self.assertEqual(partial.stat().st_size, 1024 * 1024)
                result = fetch_blob(root, digest, len(payload), [location])
                self.assertEqual(result.read_bytes(), payload)
                self.assertEqual(requested, [None, "bytes=1048576-"])
                self.assertFalse(partial.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_poisoned_prefix_restarts_good_peer_from_zero(self) -> None:
        """A valid peer must not be rejected because it inherited another peer's bad prefix."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            payload = b"correct checkpoint bytes" * 100000
            source.write_bytes(payload)
            digest, _ = store_blob(source, root / "good")
            partial = root / "target" / ".downloads" / f"{digest}.part"
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b"x" * 1024 * 1024)
            server = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_storage_handler(root / "good"))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            blamed: list[tuple[str | None, str]] = []

            try:
                location = f"http://127.0.0.1:{server.server_port}/blobs/{digest}"
                result = fetch_blob(root / "target", digest, len(payload), [location],
                                    bad_source=lambda location, digest: blamed.append((location, digest)))
                self.assertEqual(result.read_bytes(), payload)
                self.assertEqual(blamed, [])
            finally:
                server.shutdown()
                server.server_close()

    def test_download_lock_wait_can_be_cancelled(self) -> None:
        """A blocked same-blob transfer cannot delay a revoked computation lease indefinitely."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = "0" * 64
            downloads = root / ".downloads"
            downloads.mkdir()
            deadline = time.monotonic() + 0.05

            def check() -> None:
                """Revoke test ownership while another transfer still holds its file lock."""

                if time.monotonic() >= deadline:
                    raise agent.LeaseLost("test lease expired")

            with (downloads / f"{digest}.lock").open("a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)

                with self.assertRaises(agent.LeaseLost):
                    fetch_blob(root, digest, 1, [], check)

            self.assertLess(time.monotonic() - deadline, 0.2)

    def test_corrupt_or_missing_source_falls_back(self) -> None:
        """Reject wrong bytes and recover from a second independently hashed peer."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"correct checkpoint contents")
            digest, path = store_blob(source, root / "good")
            bad = blob_path(root / "bad", digest)
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"x" * source.stat().st_size)
            servers = [ThreadingHTTPServer(("127.0.0.1", 0), agent.make_storage_handler(root / name))
                       for name in ("bad", "good")]

            for server in servers:
                threading.Thread(target=server.serve_forever, daemon=True).start()

            try:
                locations = [f"http://127.0.0.1:{server.server_port}/blobs/{digest}" for server in servers]
                result = fetch_blob(root / "target", digest, source.stat().st_size, locations)
                self.assertEqual(result.read_bytes(), source.read_bytes())
                bad.unlink()
                result = fetch_blob(root / "other", digest, source.stat().st_size, locations)
                self.assertEqual(result.read_bytes(), source.read_bytes())
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()


# Exercise fencing and incarnation changes through the same leader transaction handlers.
class LeaseTests(unittest.TestCase):
    """Check stale-owner isolation, stop latching, migration, and single-owner dispatch."""

    def test_storage_only_node_cannot_claim_computation(self) -> None:
        """Keep a storage peer out of dispatch until a new compute incarnation registers."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            identity = {"node_name": "storage", "session_id": "first"}
            handler.dispatch_post("/v1/register", {**identity, "storage_only": True})
            queued = handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}})
            self.assertIsNone(handler.dispatch_post("/v1/lease", identity)["job"])

            replacement = {"node_name": "storage", "session_id": "second"}
            handler.dispatch_post("/v1/register", replacement)
            job = handler.dispatch_post("/v1/lease", replacement)["job"]
            self.assertEqual(job["run_id"], queued["run_id"])

            with self.assertRaises(PermissionError):
                handler.dispatch_post("/v1/lease", identity)

    def test_expiry_reassignment_and_stale_submissions(self) -> None:
        """Reject every old-owner mutation after expiry, including late completion."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800, lease_seconds=1)
            handler = object.__new__(leader.make_handler(database))

            with patch.object(leader.time, "time", return_value=100):
                for name in ("a", "b"):
                    handler.dispatch_post("/v1/register", {"node_name": name})

                queued = handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}})
                original = handler.dispatch_post("/v1/lease", {"node_name": "a"})["job"]
                self.assertIsNone(handler.dispatch_post("/v1/lease", {"node_name": "a"})["job"])

            with patch.object(leader.time, "time", return_value=102):
                replacement = handler.dispatch_post("/v1/lease", {"node_name": "b"})["job"]
                self.assertEqual(replacement["run_id"], queued["run_id"])
                self.assertNotEqual(replacement["lease_token"], original["lease_token"])

                for route in ("progress", "run-control", "complete", "checkpoint", "restored", "fail", "requeue"):
                    with self.assertRaises(PermissionError):
                        handler.dispatch_post(f"/v1/{route}", original)

            with leader.connect(database) as connection:
                row = connection.execute("SELECT * FROM runs").fetchone()
                self.assertEqual(row["node_name"], "b")
                self.assertEqual(row["recovery_count"], 1)
                history = list(connection.execute("SELECT * FROM lease_history ORDER BY attempt"))
                self.assertEqual([row["outcome"] for row in history], ["lease expired", "running"])

    def test_bad_blob_claim_is_removed_and_scheduled_for_repair(self) -> None:
        """Confirmed damaged content loses its replica claim without erasing snapshot history."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))

            for name in ("a", "b", "observer"):
                handler.dispatch_post("/v1/register", {"node_name": name, "address": f"http://{name}:8042"})

            specification = {"program": "demo", "arguments": {"steps": 10}}
            queued = handler.dispatch_post("/v1/enqueue", {"specification": specification})
            job = handler.dispatch_post("/v1/lease", {"node_name": "a"})["job"]
            state = b'{"next_step":2}'
            blob = hashlib.sha256(state).hexdigest()
            manifest = {"format": checkpoints.FORMAT, "run_id": queued["run_id"],
                        "calculation_id": calculation_id(specification), "program": "demo", "cursor": 1,
                        "done": 1, "total": 10,
                        "files": [{"name": "solver.checkpoint.json", "size": len(state), "sha256": blob}]}
            digest = calculation_id(manifest)
            handler.dispatch_post("/v1/checkpoint", {**job, "manifest": manifest, "manifest_hash": digest})
            handler.dispatch_post("/v1/checkpoint-replica", {"node_name": "b", "manifest_hash": digest})
            handler.dispatch_post("/v1/blob-bad", {"node_name": "observer", "source_node": "b", "blob_hash": blob})
            needed = handler.dispatch_post("/v1/replication", {"node_name": "b"})["replication"]
            self.assertEqual(needed["manifest_hash"], digest)
            self.assertEqual([peer["node_name"] for peer in needed["sources"]], ["a"])

            with leader.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM checkpoint_replicas").fetchone()[0], 1)
                self.assertIsNotNone(connection.execute("SELECT durable_at FROM checkpoints").fetchone()[0])

    def test_new_incarnation_fences_old_agent_and_preserves_history(self) -> None:
        """Reject an old heartbeat and requeue its work when a replacement agent registers."""

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            old = {"node_name": "worker", "session_id": "first"}
            new = {"node_name": "worker", "session_id": "second"}
            handler.dispatch_post("/v1/register", old)
            handler.dispatch_post("/v1/enqueue", {"specification": {"program": "demo"}})
            job = handler.dispatch_post("/v1/lease", old)["job"]
            handler.dispatch_post("/v1/register", new)

            with self.assertRaises(PermissionError):
                handler.dispatch_post("/v1/heartbeat", old)

            with self.assertRaises(PermissionError):
                handler.dispatch_post("/v1/complete", job)

            replacement = handler.dispatch_post("/v1/lease", new)["job"]
            self.assertEqual(job["run_id"], replacement["run_id"])
            self.assertNotEqual(job["lease_token"], replacement["lease_token"])
            leader.initialize(database, 1800)

            with leader.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM lease_history").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT state FROM runs").fetchone()[0], "running")

    def test_generation_aware_batched_revalidation(self) -> None:
        """Reuse metadata on the same disk but demand hashes after storage replacement."""
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "leader.sqlite"
            leader.initialize(database, 1800)
            handler = object.__new__(leader.make_handler(database))
            generation = "11111111-1111-4111-8111-111111111111"
            old = {"node_name": "worker", "session_id": "first",
                   "storage_generation": generation, "address": "http://worker:8042"}
            handler.dispatch_post("/v1/register", old)
            digest = "a" * 64
            with leader.connect(database) as connection:
                connection.execute(
                    "INSERT INTO artifacts(artifact_hash,target_replicas,created,size) VALUES(?,3,1,9)",
                    (digest,))
                connection.execute(
                    "INSERT INTO replicas(artifact_hash,node_name,location,created) VALUES(?,?,?,1)",
                    (digest, "worker", f"http://worker:8042/blobs/{digest}"))
            new = {**old, "session_id": "second"}
            registration = handler.dispatch_post("/v1/register", new)
            self.assertEqual(registration["storage_validation_mode"], "metadata")
            batch = handler.dispatch_post("/v1/revalidation-batch", new)
            self.assertEqual(batch["mode"], "metadata")
            self.assertEqual(batch["records"][0]["members"],
                             [{"digest": digest, "size": 9}])
            result = handler.dispatch_post("/v1/revalidation-batch-done", {
                **new, "records": [{"kind": "artifact", "digest": digest, "valid": True}],
            })
            self.assertEqual(result["remaining"], 0)
            with leader.connect(database) as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM replicas WHERE node_name='worker'").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT storage_validation_mode FROM nodes WHERE node_name='worker'").fetchone()[0],
                    "verified")

            replaced = {**new, "session_id": "third",
                        "storage_generation": "22222222-2222-4222-8222-222222222222"}
            registration = handler.dispatch_post("/v1/register", replaced)
            self.assertEqual(registration["storage_validation_mode"], "hash")


# Validate real DP snapshots and failover through live agent processes.
class RecoveryTests(unittest.TestCase):
    """Run actual C computations across private local worker processes."""

    def test_dp_worker_loss_recovers_on_smaller_worker(self) -> None:
        """Kill the originating agent and finish byte-identically from its replicated DP cursor."""

        with tempfile.TemporaryDirectory(prefix="kh-failover-") as directory:
            root = Path(directory)
            cluster = Cluster(root, lease_seconds=5)

            try:
                origin = cluster.worker("origin", 2)
                wait_until(lambda: len(request_json(cluster.url, "GET", "/v1/status")["nodes"]) == 1,
                           "origin registration")
                specification = {"program": "dp", "arguments": {
                    "p": 11, "r": 5, "threads": min(2, len(os.sched_getaffinity(0))),
                    "tile_side": 512, "progress_milliseconds": 50,
                    "max_state_bytes": 100_000_000, "max_visits": 3_000_000_000,
                }}
                queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": specification})
                first = wait_until(lambda: (row if (row := find_run(cluster.url, queued["run_id"]))["state"] == "running" else None),
                                   "origin lease")
                cluster.worker("replacement", 1)

                def replicated() -> dict[str, object] | None:
                    """Return a nonterminal run with a genuinely replicated partial DP snapshot."""

                    row = find_run(cluster.url, queued["run_id"])
                    return row if row["state"] == "running" and 0 < row["replicated_checkpoint_done"] < 1331**2 else None

                durable = wait_until(replicated, "partial DP checkpoint with two replicas", timeout=30)
                self.assertGreaterEqual(durable["checkpoint_replicas"], 2)
                origin.kill()
                origin.wait(timeout=3)
                completed = wait_until(
                    lambda: (row if (row := find_run(cluster.url, queued["run_id"]))["state"] == "complete" else None),
                    "DP completion after whole-worker loss", timeout=60,
                )
                self.assertEqual(completed["node_name"], "replacement")
                self.assertGreater(completed["restored_done"], 0)
                self.assertEqual(completed["recovery_count"], 1)
                self.assertEqual(completed["lease_attempt"], 2)
                self.assertNotEqual(completed["lease_token"], first["lease_token"])
                replacement = root / "replacement" / "work" / queued["run_id"] / completed["lease_token"]
                reference = root / "reference"
                artifact = root / "reference.json"
                subprocess.run([str(ROOT.parent / "dp_solver" / "kh_dp_local"), "11", "5",
                                "--work-dir", str(reference), "--tile-side", "512", "--raw-transitions",
                                "-o", str(artifact)], check=True, capture_output=True, timeout=30)

                for name in ("values.bin", "choices.bin"):
                    self.assertEqual((reference / name).read_bytes(), (replacement / "dp-state" / name).read_bytes())

                self.assertEqual(artifact.read_bytes(), (replacement / "result.bin").read_bytes())
                self.assertEqual(json.loads(artifact.read_bytes())["theta"], 4181)

                # Former owners cannot overwrite the result even after their machine returns.
                with self.assertRaises(HTTPError) as rejected:
                    request_json(cluster.url, "POST", "/v1/complete", {
                        "run_id": queued["run_id"], "lease_token": first["lease_token"],
                        "artifact_hash": "0" * 64, "artifact_location": "stale",
                    })

                self.assertEqual(rejected.exception.code, 409)
                self.assertTrue((root / "origin" / "work" / queued["run_id"] / first["lease_token"]).exists())
                self.assertTrue((root / "replacement" / "blobs").exists())

                with leader.connect(cluster.database) as connection:
                    history = list(connection.execute("SELECT * FROM lease_history ORDER BY attempt"))
                    self.assertEqual([row["outcome"] for row in history], ["lease expired", "complete"])
                    self.assertGreater(history[1]["restored_done"], 0)
                    self.assertGreater(connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0], 1)
            except Exception:
                for log in root.glob("*.log"):
                    print(f"{log.name}:\n{log.read_text()[-12000:]}", file=sys.stderr)
                raise
            finally:
                cluster.close()

    def test_corrupt_latest_snapshot_uses_older_valid_checkpoint(self) -> None:
        """Reject corrupted newest bytes, then restore an earlier exact DP boundary."""

        with tempfile.TemporaryDirectory(prefix="kh-fallback-") as directory:
            root = Path(directory)
            cluster = Cluster(root)
            storage = root / "source-blobs"
            storage.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_storage_handler(storage))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            stop = threading.Event()
            node = {"node_name": "source", "session_id": "manual", "address": f"http://127.0.0.1:{server.server_port}"}
            request_json(cluster.url, "POST", "/v1/register", node)

            def heartbeat() -> None:
                """Keep the storage node healthy while its unrenewed computation lease expires."""

                while not stop.wait(0.1):
                    request_json(cluster.url, "POST", "/v1/heartbeat", node)

            heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
            heartbeat_thread.start()

            try:
                specification = {"program": "dp", "arguments": {"p": 5, "r": 3, "tile_side": 7, "threads": 2}}
                queued = request_json(cluster.url, "POST", "/v1/enqueue", {"specification": specification})
                job = request_json(cluster.url, "POST", "/v1/lease", node)["job"]
                source = root / "source-work"
                source.mkdir()
                command = [str(ROOT.parent / "dp_solver" / "kh_dp_local"), "5", "3",
                           "--work-dir", str(source / "dp-state"), "--tile-side", "7",
                           "-o", str(source / "result.bin"), "--stop-after-tiles", "1"]
                manifests: list[dict[str, object]] = []

                for cursor in (1, 2):
                    self.assertEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 75)
                    manifest = checkpoints.capture_checkpoint(specification, queued["run_id"], source, storage, cursor)
                    manifests.append(manifest)
                    request_json(cluster.url, "POST", "/v1/checkpoint", {
                        **job, "manifest": manifest, "manifest_hash": calculation_id(manifest),
                    })

                newest = next(record for record in manifests[-1]["files"] if record["name"] == "checkpoint.bin")
                blob_path(storage, newest["sha256"]).write_bytes(b"x" * newest["size"])

                # A mismatched layout must fail before installing any mutable state.
                incompatible = copy.deepcopy(manifests[0])
                incompatible["layout"]["byteorder"] = "big" if sys.byteorder == "little" else "little"

                with self.assertRaisesRegex(ValueError, "incompatible"):
                    checkpoints.validate_manifest(incompatible, specification, queued["run_id"], require_native=True)

                cluster.worker("replacement", 1)
                completed = wait_until(
                    lambda: (row if (row := find_run(cluster.url, queued["run_id"]))["state"] == "complete" else None),
                    "fallback to older checkpoint", timeout=30,
                )
                self.assertEqual(completed["restored_hash"], calculation_id(manifests[0]))
                self.assertEqual(completed["restored_done"], 49)
                result = root / "replacement" / "work" / queued["run_id"] / completed["lease_token"] / "result.bin"
                subprocess.run([sys.executable, str(ROOT.parent / "dp_solver" / "verify_dp.py"), str(result)],
                               check=True, capture_output=True, timeout=10)

                # Wrong run identities and over-budget manifests are rejected before capture/restore.
                with self.assertRaises(ValueError):
                    checkpoints.validate_manifest(manifests[0], specification, "another-run")

                with self.assertRaises(ValueError):
                    checkpoints.validate_manifest(manifests[0], specification, queued["run_id"], max_bytes=1)
            except Exception:
                for log in root.glob("*.log"):
                    print(f"{log.name}:\n{log.read_text()[-12000:]}", file=sys.stderr)
                raise
            finally:
                stop.set()
                heartbeat_thread.join(timeout=3)
                server.shutdown()
                server.server_close()
                cluster.close()


# An explicit test run avoids surprising work from an empty command line.
if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Test replicated checkpoints and cross-worker recovery.\nExample: python3 tests/test_recovery.py --run")
    else:
        unittest.main(argv=[sys.argv[0], *[arg for arg in sys.argv[1:] if arg != "--run"]])
