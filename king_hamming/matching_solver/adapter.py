"""Pinned primitive-X matching attempts behind the generic cluster adapter contract."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import sys

from adapters import SolverAdapter
from dp_solver.artifacts import decode_dp
from matching_solver import cluster_checkpoints, prototype_checkpoint
from matching_solver.artifacts import primitive, request_count, verify

ROOT = Path(__file__).resolve().parent.parent
MAX_INLINE_DP = 700_000


def distributed_memory_required(dp: dict, workers: int = 9,
                                threads: int = 16) -> tuple[int, int]:
    """Return native coordinator and replicated-shard allocation bounds."""
    count, q, reserve = request_count(dp), dp["q"], 67_108_864
    bit_count, bit_q = (count + 7) // 8, (q + 7) // 8
    limit = min(count, 262_144)
    coordinator_base = 18 * count + 4 * q
    coordinator_scratch = max(
        bit_count + bit_q + 2 * workers * limit * 12,
        (workers + 1) * bit_q,
    )
    shard_base = 10 * count + bit_count + 8 * q + bit_q
    shard_scratch = max(
        4 * count + 4 * ((count + workers - 1) // workers) +
        threads * (65_536 * 4 + 16),
        bit_count + bit_q + limit * 12 + threads * 16 * (limit + 1),
        workers * limit * 12,
    )
    return (coordinator_base + coordinator_scratch + reserve,
            shard_base + shard_scratch + reserve)


# Bound the queue request and bind each attempt to exact saved KHD1 bytes.
def decode_input(specification: dict) -> tuple[dict, bytes, bytes]:
    """Return validated DP, exact bytes, and hash for one pinned matching specification."""
    arguments = specification.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError("matching arguments must be an object")
    encoded = arguments.get("dp_b64")
    expected = arguments.get("dp_sha256")
    if not isinstance(encoded, str) or len(encoded) > (MAX_INLINE_DP * 4 + 2) // 3 + 4:
        raise ValueError("matching DP input exceeds inline queue limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("invalid base64 DP input") from error
    if len(raw) > MAX_INLINE_DP or base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("matching DP input is oversized or noncanonical")
    digest = hashlib.sha256(raw).digest()
    if not isinstance(expected, str) or expected != digest.hex():
        raise ValueError("matching DP SHA-256 mismatch")
    return decode_dp(raw), raw, digest


# Keep mathematical identity separate from worker thread allocation.
class MatchingAdapter(SolverAdapter):
    """Run one pinned field attempt and retain either a matching or certified Hall obstruction."""

    programs = ("match", "match_distributed")

    def validate(self, specification, internal=False):
        """Reject malformed input, nonprimitive fields and resource controls."""
        super().validate(specification, internal)
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        polynomial = arguments.get("poly")
        if not isinstance(polynomial, list) or any(type(value) is not int for value in polynomial) or not primitive(dp["p"], dp["r"], polynomial):
            raise ValueError("matching polynomial must be primitive with generator X")
        threads = arguments.get("threads", 1)
        maximum = arguments.get("max_bytes", 2**31)
        if type(threads) is not int or not 1 <= threads <= 1024 or type(maximum) is not int or not 1 <= maximum <= 2**40:
            raise ValueError("invalid matching thread or memory limit")
        if specification["program"] == "match_distributed":
            edges = arguments.get("max_edges", 2_000_000)
            elements = arguments.get("max_field_elements", 1_000_000)
            workers = arguments.get("workers", 2)
            if (type(edges) is not int or not 1 <= edges <= 1_000_000_000_000_000 or
                    type(elements) is not int or not 1 <= elements <= 2**32 - 1 or
                    dp["q"] > elements or type(workers) is not int or not 2 <= workers <= 256 or
                    workers * threads > 256):
                raise ValueError("distributed matching edge or field admission limit exceeded")

    def required_nodes(self, specification):
        """Reserve the requested coordinator and partner group for a field attempt."""
        return (specification["arguments"].get("workers", 2)
                if specification["program"] == "match_distributed" else 1)

    def resource_requirements(self, specification):
        """Account for the coordinator and shard colocated on the lease owner."""
        arguments = specification["arguments"]
        if specification["program"] != "match_distributed":
            return {"coordinator_memory_bytes": int(arguments.get("max_bytes", 2**31)),
                    "worker_memory_bytes": 0, "min_cpu_count": 1}
        dp, _, _ = decode_input(specification)
        coordinator, shard = distributed_memory_required(
            dp, int(arguments.get("workers", 2)), int(arguments.get("threads", 1)))
        return {"coordinator_memory_bytes": coordinator + shard,
                "worker_memory_bytes": shard, "min_cpu_count": 1}

    def retry_elsewhere(self, specification):
        """Requeue a lost group under a newly fenced reservation rather than restarting stale pipes."""
        return specification["program"] == "match_distributed"

    def describe(self, specification):
        """Return the saved field and pinned polynomial for operator status."""
        dp, _, _ = decode_input(specification)
        polynomial = ",".join(map(str, specification["arguments"]["poly"]))
        return f"{dp['p']}^{dp['r']} {specification['program']} poly={polynomial}"

    def status_details(self, specification, run, status):
        """Describe conceptual permutation size and distributed shard occupancy."""
        dp, _, _ = decode_input(specification)
        rows = dp["theta"] * dp["f"] * dp["f"] + dp["q"]
        entries = rows * (dp["q"] + 1)
        details = [f"permutation={rows:,} x {dp['q'] + 1:,} "
                   f"({entries:,} entries; {_byte_size(entries * 4)} at uint32)"]
        if specification["program"] == "match_distributed":
            total = int(specification["arguments"].get("workers", 0))
            complete = total if run.get("state") == "complete" else 0
            active = sum(node.get("reserved_for") == run["run_id"] or
                         (run.get("state") == "running" and
                          node.get("node_name") == run.get("node_name"))
                         for node in status.get("nodes", []))
            details.append(
                f"shards={complete} complete, {total - complete} pending, {active} active")
        return details

    def estimate(self, specification, rate):
        """Estimate search work from request count and field degree."""
        dp, _, _ = decode_input(specification)
        return max(1, request_count(dp) * dp["f"] * dp["r"]) / rate

    def worker_specification(self, specification, cpus):
        """Cap pinned workers to assigned CPU affinity while preserving field identity."""
        result = json.loads(json.dumps(specification))
        arguments = result["arguments"]
        if specification["program"] == "match_distributed":
            arguments["local_threads"] = min(arguments.get("threads", 1), len(cpus))
        else:
            arguments["threads"] = min(arguments.get("threads", 1), len(cpus))
        return result

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        """Return argv for the matching bridge under a private run directory."""
        arguments = specification["arguments"]
        if specification["program"] == "match_distributed":
            command = [sys.executable, str(ROOT / "matching_solver/native_coordinator.py"),
                       str(output.parent / "input.khdp"), "--output", str(output),
                       "--checkpoint-dir", str(output.parent / "phase-state"),
                       "--poly", ",".join(map(str, arguments["poly"])),
                       "--max-edges", str(arguments.get("max_edges", 2_000_000)),
                       "--max-field-elements", str(arguments.get("max_field_elements", 1_000_000)),
                       "--max-bytes", str(arguments.get("max_bytes", 2**31)),
                       "--threads-per-worker", str(arguments.get("threads", 1)),
                       "--checkpoint-seconds", str(checkpoint_seconds),
                       "--restart-attempts", "0"]
            command.extend(["--worker", "local"])
            return command
        return [sys.executable, str(ROOT / "matching_solver/cluster_solver.py"),
                "--dp", str(output.parent / "input.khdp"), "--output", str(output),
                "--checkpoint", str(checkpoint),
                "--poly", ",".join(map(str, arguments["poly"])),
                "--threads", str(arguments.get("threads", 1)),
                "--max-bytes", str(arguments.get("max_bytes", 2**31)),
                "--checkpoint-seconds", str(checkpoint_seconds)]

    def prepare(self, specification, directory, job, leader):
        """Stage exact immutable DP bytes and opt into restored native state if present."""
        _, raw, _ = decode_input(specification)
        (directory / "input.khdp").write_bytes(raw)
        if specification["program"] == "match_distributed":
            partners = job.get("reserved_workers", [])
            expected = specification["arguments"].get("workers", 2) - 1
            if len(partners) != expected:
                raise ValueError("distributed matching lease has the wrong reserved partner count")
            commands = []
            requested_threads = specification["arguments"].get("threads", 1)
            worker_threads = [specification["arguments"].get("local_threads", requested_threads)]
            for partner in partners:
                address = urlparse(partner["address"])
                if address.scheme != "http" or not address.hostname or not address.port:
                    raise ValueError("reserved partner has no usable peer address")
                commands.extend(["--worker", partner["address"]])
                cpu_count = len([cpu for cpu in partner.get("cpu_set", "").split(",") if cpu])
                if cpu_count < 1:
                    raise ValueError("reserved partner has no usable CPU allocation")
                worker_threads.append(min(requested_threads, cpu_count))
            commands.extend(["--peer-run-id", job["run_id"],
                             "--peer-lease-token", job["lease_token"],
                             "--worker-threads", ",".join(map(str, worker_threads))])
            if (directory / "solver.checkpoint.json").exists():
                commands.extend(["--resume", str(directory / "solver.checkpoint.json")])
            return commands
        return ["--resume"] if (directory / "solver.checkpoint.json").exists() else []

    def peer_command(self, specification, cpus, worker_index=1, worker_count=2):
        """Launch the authorized matching shard on a reserved partner agent."""
        if specification["program"] != "match_distributed":
            raise ValueError("single-host matching has no peer worker")
        nodes = self.required_nodes(specification)
        if worker_count != nodes or not 1 <= worker_index < worker_count:
            raise ValueError("reserved matching shard identity is invalid")
        threads = min(specification["arguments"].get("threads", 1), len(cpus))
        if threads < 1:
            raise ValueError("reserved matching shard has no allocated CPU")
        return [str(ROOT / "matching_solver/kh_match_worker"),
                "--index", str(worker_index), "--count", str(worker_count),
                "--cpus", ",".join(map(str, cpus[:threads]))]

    def checkpoint_handshake(self, specification):
        """Require immutable phase snapshots before acknowledging the native solver."""
        return True

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        """Describe committed local KHC1 or distributed KHS1 state."""
        dp, _, digest = decode_input(specification)
        if specification["program"] == "match_distributed":
            path = directory / "phase-state" / f"phase-{cursor:020d}.khstate"
            phase, matched, _ = prototype_checkpoint.load(path, dp, digest, specification["arguments"]["poly"])
            if phase != cursor:
                raise ValueError("distributed checkpoint phase cursor mismatch")
            manifest.update(layout="KHS1", dp_sha256=digest.hex(),
                            polynomial=specification["arguments"]["poly"],
                            parameters=prototype_checkpoint.parameters(dp),
                            done=matched, total=request_count(dp))
            return {prototype_checkpoint.NAME: path}
        path = directory / "solver.checkpoint.json"
        metadata = cluster_checkpoints.inspect(path, dp, digest, specification["arguments"]["poly"])
        if metadata["cursor"] != cursor:
            raise ValueError("matching checkpoint phase cursor mismatch")
        manifest.update(layout="KHC1", dp_sha256=digest.hex(), polynomial=specification["arguments"]["poly"],
                        parameters=cluster_checkpoints.parameters(dp),
                        done=metadata["done"], total=metadata["total"])
        return {cluster_checkpoints.NAME: path}

    def checkpoint_description(self, manifest, specification, require_native=False):
        """Check manifest identity, coverage and exact KHC1 byte count."""
        dp, _, digest = decode_input(specification)
        layout = "KHS1" if specification["program"] == "match_distributed" else "KHC1"
        if manifest.get("layout") != layout or manifest.get("dp_sha256") != digest.hex() or manifest.get("polynomial") != specification["arguments"]["poly"]:
            raise ValueError("matching checkpoint manifest identity mismatch")
        expected_parameters = (prototype_checkpoint.parameters(dp) if layout == "KHS1"
                               else cluster_checkpoints.parameters(dp))
        if manifest.get("parameters") != expected_parameters:
            raise ValueError("matching checkpoint graph input mismatch")
        done = manifest.get("done")
        cursor = manifest.get("cursor")
        total = request_count(dp)
        if type(done) is not int or not 0 <= done <= total or type(cursor) is not int or cursor < 1:
            raise ValueError("matching checkpoint coverage mismatch")
        if layout == "KHS1":
            size = prototype_checkpoint.HEADER.size + 4 * (dp["r"] + 1) + 8 * total + 32
            return {prototype_checkpoint.NAME: size}, done, total
        return {cluster_checkpoints.NAME: cluster_checkpoints.size(dp)}, done, total

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        """Check native stream identity, exact graph blocks, coverage and checksum."""
        if manifest["layout"] == "KHS1":
            prototype_checkpoint.inspect_manifest(paths[prototype_checkpoint.NAME], manifest)
            return
        from matching_solver.cluster_checkpoints import inspect_manifest
        inspect_manifest(paths[cluster_checkpoints.NAME], manifest)

    def checkpoint_destination(self, directory):
        """Install one checkpoint file at the native solver's private work path."""
        return directory / "solver.checkpoint.json", "matching.checkpoint"

    def runtime_files(self):
        """Bundle cluster bridges, codecs, and compiled native kernels on workers."""
        names = ("adapter.py", "artifacts.py", "cluster_checkpoints.py", "cluster_solver.py",
                 "native_coordinator.py", "polynomials.py", "prototype_checkpoint.py",
                 "worker_transport.py")
        package = [(ROOT / "matching_solver" / name, "matching_solver/" + name) for name in names]
        return package + [
            (ROOT / "matching_solver/kh_match_kernel", "matching_solver/kh_match_kernel"),
            (ROOT / "matching_solver/kh_match_worker", "matching_solver/kh_match_worker"),
            (ROOT / "matching_solver/kh_match_distributed", "matching_solver/kh_match_distributed"),
        ]

    def validate_result(self, specification, output):
        """Independently validate the published KHM1 matching or Hall certificate."""
        dp, _, digest = decode_input(specification)
        summary = verify(output, dp, digest, specification["arguments"].get("max_bytes", 2**31))
        if summary["polynomial"] != specification["arguments"]["poly"]:
            raise ValueError("matching result polynomial differs from pinned job")


def _byte_size(value: int) -> str:
    """Format conceptual matching output size without allocating it."""
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f}{unit}" if unit != "B" else f"{value}B"
        amount /= 1024
    raise AssertionError("unreachable")
