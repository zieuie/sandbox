"""Single-GPU exact matching (`match_gpu`) behind the generic cluster adapter contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from matching_solver.adapter import MatchingAdapter, decode_input
from matching_solver.artifacts import request_count

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024**2


def device_bytes(dp: dict) -> int:
    """Device allocation bound; mirrors device_required in src/main.c."""
    n, q = request_count(dp), dp["q"]
    return 8 * q + 24 * n + 18 * (n // 8 + 1) + 16 * len(dp["runs"]) + 16 * MIB


def host_bytes(dp: dict, threads: int = 4) -> int:
    """Host bound for the kernel (field build, downloads) and the Python KHM1 verifier."""
    n, q = request_count(dp), dp["q"]
    kernel = 4 * q + 4 * dp["f"] * dp["p"] * threads + 8 * MIB * threads + 4 * n + 64 * MIB
    verifier = 24 * q + 512 * MIB  # measured ~18-21 bytes/label for 2^25..3^17
    return max(kernel, verifier)


class GPUMatchingAdapter(MatchingAdapter):
    """Run one pinned field attempt on one fenced GPU; seconds, so no checkpoints."""

    programs = ("match_gpu",)
    max_requests = 2**32 - 2   # request indices are 32-bit in src/main.c

    def validate(self, specification, internal=False):
        """Reuse pinned-field validation; add the kernel's 16-bit choice bound."""
        super().validate(specification, internal)
        dp, _, _ = decode_input(specification)
        if dp["f"] > 65535 or request_count(dp) > self.max_requests:
            raise ValueError(f"GPU matching requires F <= 65535 and at most {self.max_requests} requests")

    def required_nodes(self, specification):
        return 1

    def resource_requirements(self, specification):
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        resources = {"coordinator_memory_bytes": int(arguments.get("max_bytes", host_bytes(dp))),
                     "worker_memory_bytes": 0, "min_cpu_count": 1,
                     "gpu_memory_bytes": device_bytes(dp)}
        if arguments.get("require_known_capacity"):
            resources["require_known_capacity"] = True
        return resources

    def allows_host_sharing(self, specification):
        """Field construction uses a few CPUs; the GPU is fenced separately by the leader."""
        return True

    def cpu_width(self, specification, available):
        return min(available, max(1, int(specification["arguments"].get("threads", 1))))

    def retry_elsewhere(self, specification):
        return True

    def estimate(self, specification, rate):
        dp, _, _ = decode_input(specification)
        return max(1, request_count(dp) * dp["f"]) / (rate * 100)

    def worker_specification(self, specification, cpus):
        result = json.loads(json.dumps(specification))
        result["arguments"]["threads"] = max(1, min(result["arguments"].get("threads", 1), len(cpus)))
        return result

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        arguments = specification["arguments"]
        return [sys.executable, str(ROOT / "gpu_match_solver/cluster_solver.py"),
                "--dp", str(output.parent / "input.khdp"), "--output", str(output),
                "--poly", ",".join(map(str, arguments["poly"])),
                "--threads", str(arguments.get("threads", 1)),
                "--max-bytes", str(arguments.get("max_bytes", 2**31))]

    def prepare(self, specification, directory, job, leader):
        _, raw, _ = decode_input(specification)
        (directory / "input.khdp").write_bytes(raw)
        return []

    def checkpoint_handshake(self, specification):
        return False

    def checkpoint_paths(self, specification, directory, cursor, manifest):
        raise ValueError("GPU matching has no checkpoints")

    def checkpoint_description(self, manifest, specification, require_native=False):
        raise ValueError("GPU matching has no checkpoints")

    def validate_checkpoint_metadata(self, manifest, paths, require_native=True):
        raise ValueError("GPU matching has no checkpoints")

    def runtime_files(self):
        """GPU kernels, the driver probe, and their bridges; CPU matching files come from MatchingAdapter."""
        return [
            (ROOT / "gpu_match_solver/__init__.py", "gpu_match_solver/__init__.py"),
            (ROOT / "gpu_match_solver/adapter.py", "gpu_match_solver/adapter.py"),
            (ROOT / "gpu_match_solver/cluster_solver.py", "gpu_match_solver/cluster_solver.py"),
            (ROOT / "gpu_match_solver/submit.py", "gpu_match_solver/submit.py"),
            (ROOT / "gpu_match_solver/kh_gpu_match_kernel", "gpu_match_solver/kh_gpu_match_kernel"),
            (ROOT / "gpu_dp_solver/kh_gpu_dp_tile", "gpu_dp_solver/kh_gpu_dp_tile"),
            (ROOT / "cuda/kh_cuda_probe", "cuda/kh_cuda_probe"),
        ]
