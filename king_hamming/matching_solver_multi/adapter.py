"""Lease-fenced partitioned matching behind the ordinary cluster lifecycle."""
from __future__ import annotations
import hashlib
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import urlopen

from adapters import SolverAdapter
from matching_solver.adapter import decode_input, MatchingAdapter
from matching_solver.artifacts import request_count, verify
from matching_solver_multi import resources, state

ROOT = Path(__file__).resolve().parent.parent


class PartitionedAdapter(SolverAdapter):
    programs = ("match_partitioned",)

    def validate(self, specification, internal=False):
        super().validate(specification, internal)
        # Reuse exact DP/primitive-field validation, not distributed replication
        # resource controls. This solver has a different owned-state envelope.
        MatchingAdapter().validate({"program": "match", "arguments": specification["arguments"]})
        dp, _, _ = decode_input(specification)
        a = specification["arguments"]
        limits = {"workers": (2, 16), "threads": (1, 64), "batch": (1, 1048576),
                  "memory_margin_percent": (10, 100), "owner_max_bytes": (1, 2**40),
                  "coordinator_max_bytes": (1, 2**40), "max_field_elements": (1, 2**32-1),
                  "max_edges": (1, 10**15)}
        for key, (low, high) in limits.items():
            if type(a.get(key)) is not int or not low <= a[key] <= high:
                raise ValueError(f"invalid partitioned control: {key}")
        if type(a.get("checkpoint_phases", 0)) is not int or not 0 <= a.get("checkpoint_phases", 0) <= 2**32-1:
            raise ValueError("invalid partitioned phase checkpoint interval")
        if dp["q"] > a["max_field_elements"] or request_count(dp) * dp["f"] > a["max_edges"]:
            raise ValueError("partitioned graph exceeds field/edge limit")
        coordinator, worker = resources.admitted_memory(dp, a["workers"], a["threads"], a["batch"], a["memory_margin_percent"])
        if worker > a["owner_max_bytes"] or coordinator > a["coordinator_max_bytes"]:
            raise ValueError("partitioned memory admission limit exceeded")

    def required_nodes(self, specification):
        return specification["arguments"]["workers"]

    def resource_requirements(self, specification):
        a = specification["arguments"]
        return dict(coordinator_memory_bytes=a["coordinator_max_bytes"], worker_memory_bytes=a["owner_max_bytes"],
                    min_cpu_count=1, require_known_capacity=True)

    def retry_elsewhere(self, specification):
        return True

    def checkpoint_handshake(self, specification):
        return True

    def describe(self, specification):
        dp, _, _ = decode_input(specification)
        return f"{dp['p']}^{dp['r']} partitioned matching ({self.required_nodes(specification)} owners)"

    def estimate(self, specification, rate):
        dp, _, _ = decode_input(specification)
        return max(1, request_count(dp) * dp["f"] * dp["r"]) / rate

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        return [sys.executable, str(ROOT / "matching_solver_multi/coordinator.py"),
                "--specification", str(output.parent / "specification.json"),
                "--participants", str(output.parent / "participants.json"), "--output", str(output),
                "--checkpoint-seconds", str(checkpoint_seconds)]

    def prepare(self, specification, directory, job, leader):
        participants = job.get("reserved_workers", [])
        if len(participants) != self.required_nodes(specification) - 1:
            raise ValueError("partitioned lease has wrong group size")
        with urlopen(leader + "/v1/status", timeout=15) as response:
            nodes = json.load(response)["nodes"]
        local = next(node for node in nodes if node["node_name"] == job["node_name"])
        rows = []
        for index, node in enumerate([local, *participants]):
            address = urlparse(node["address"])
            if address.scheme != "http" or not address.port:
                raise ValueError("partitioned participant lacks an HTTP endpoint")
            ipaddress.IPv4Address(address.hostname)
            cpus = job.get("assigned_cpu_set", node["cpu_set"]) if index == 0 else node["cpu_set"]
            if not cpus:
                raise ValueError("partitioned participant lacks allocated CPUs")
            rows.append(dict(host=address.hostname, cpus=cpus, address=node["address"] if index else None))
        (directory / "specification.json").write_text(json.dumps(specification))
        (directory / "participants.json").write_text(json.dumps(rows))
        return ["--run-id", job["run_id"], "--lease-token", job["lease_token"]]

    def peer_command(self, specification, cpus, worker_index=1, worker_count=2):
        if worker_count != self.required_nodes(specification) or not 1 <= worker_index < worker_count or not cpus:
            raise ValueError("invalid partitioned peer allocation")
        identity = hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return [sys.executable, str(ROOT / "matching_solver_multi/peer.py"), "--rank", str(worker_index),
                "--workers", str(worker_count), "--cpus", ",".join(map(str, cpus)), "--spec-hash", identity]

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        dp, _, digest = decode_input(specification)
        a = specification["arguments"]
        paths = {state.name(i): directory / "owner-state" / state.name(i) for i in range(a["workers"])}
        phase, done = state.validate(paths, dp, digest, a["poly"], a["workers"], cursor)
        manifest.update(layout="KMP1", parameters=dp, dp_sha256=digest.hex(), polynomial=a["poly"],
                        workers=a["workers"], done=done, total=request_count(dp))
        return paths

    def checkpoint_description(self, manifest, specification, require_native=False):
        dp, _, digest = decode_input(specification)
        a = specification["arguments"]
        if (manifest.get("layout"), manifest.get("parameters"), manifest.get("dp_sha256"),
                manifest.get("polynomial"), manifest.get("workers")) != ("KMP1", dp, digest.hex(), a["poly"], a["workers"]):
            raise ValueError("partitioned checkpoint identity mismatch")
        total = request_count(dp)
        if type(manifest.get("done")) is not int or not 0 <= manifest["done"] <= total:
            raise ValueError("invalid partitioned checkpoint progress")
        if type(manifest.get("cursor")) is not int or not 0 <= manifest["cursor"] <= total:
            raise ValueError("invalid partitioned checkpoint phase")
        return {state.name(i): state.size(dp, a["workers"], i) for i in range(a["workers"])}, manifest["done"], total

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        state.validate(paths, manifest["parameters"], bytes.fromhex(manifest["dp_sha256"]), manifest["polynomial"],
                       manifest["workers"], manifest["cursor"], manifest["done"])

    def checkpoint_destination(self, directory):
        return directory / "owner-state", None

    def validate_result(self, specification, output):
        dp, _, digest = decode_input(specification)
        summary = verify(output, dp, digest, specification["arguments"]["coordinator_max_bytes"])
        if summary["polynomial"] != specification["arguments"]["poly"]:
            raise ValueError("partitioned result polynomial mismatch")

    def runtime_files(self):
        names = ("adapter.py", "coordinator.py", "peer.py", "resources.py", "state.py", "run.py", "kh_match_multi", "kh_check_images")
        return [(ROOT / "matching_solver_multi" / name, "matching_solver_multi/" + name) for name in names]
