"""Block GPU matching (`match_gpu_blocks`) for fields larger than one GPU, behind the cluster adapter contract."""

from __future__ import annotations

from pathlib import Path

from gpu_match_solver.adapter import GPUMatchingAdapter
from matching_solver.adapter import decode_input
from matching_solver.artifacts import native_memory, request_count

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024**2
EXTRA_CELLS = 256                 # mirrors EXTRA_MAX in src/main.c
MIN_DEVICE_BYTES = 512 * MIB      # smallest GPU lease worth planning blocks on
MAX_Q = 2**36 - 1                 # FP_MAX_Q - 1 in src/field_prefix.h
STAGING_BYTES = 1024 * MIB        # per-block host staging; the kernel needs at most device budget / 8


def breakpoints(dp: dict) -> int:
    """Row breakpoints per stored cell: labels are 32-bit words plus (q-1) >> 32 breakpoints."""
    return (dp["q"] - 1) >> 32


def host_bytes(dp: dict, threads: int = 4, choice_file: bool = True) -> int:
    """Host bound for the block kernel and the native KHM1 verifier; mirrors main() in src/main.c.

    The kernel stores only the rows of the cells requests use (4*F per cell plus breakpoints),
    the final right-endpoint bitmap (q/8), and per-block staging. Its 2-byte-per-request choices
    live in a scratch file (the bridge passes --choice-file), so they need disk, not RAM.
    """
    n, q, f, p = request_count(dp), dp["q"], dp["f"], dp["p"]
    limit = max(run["a"] for run in dp["runs"]) * f
    nbp = breakpoints(dp)
    field = 4 * limit * f + 4 * limit * nbp + 4 * (threads + nbp + 1) * p * f + 4 * p * dp["r"]
    kernel = (field + (0 if choice_file else 2 * n) + (q + 7) // 8 + STAGING_BYTES +
              8 * MIB * threads + 256 * MIB)
    return max(kernel, native_memory(dp, q, f))


def block_cost(dp: dict, cells: int, requests: int) -> int:
    """Device bytes for a block of this many cells and requests; mirrors block_cost() in src/main.c."""
    return (4 * (dp["f"] + breakpoints(dp)) * (cells + EXTRA_CELLS) + 31 * (requests + requests // 64 + 64) +
            4 * requests + 16 * MIB)


def block_count(dp: dict, device_bytes: int) -> int | None:
    """Fewest equal blocks that fit device_bytes (an estimate: the kernel cuts on whole cells), or None."""
    n = request_count(dp)
    cells = max(run["a"] for run in dp["runs"]) * dp["f"]
    for count in range(1, cells + 1):
        if block_cost(dp, -(-cells // count), -(-n // count)) <= device_bytes:
            return count
    return None


class GPUBlockMatchingAdapter(GPUMatchingAdapter):
    """Run one pinned field attempt on one fenced GPU, one block at a time; minutes, so no checkpoints."""

    programs = ("match_gpu_blocks",)
    max_q = MAX_Q
    max_requests = MAX_Q

    def validate(self, specification, internal=False):
        """Reuse pinned-field validation; add the block kernel's 16-bit choice bound and device size."""
        super().validate(specification, internal)
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        device = arguments.get("gpu_memory_bytes", MIN_DEVICE_BYTES)
        if dp["f"] > 65534:
            raise ValueError("block GPU matching requires F <= 65534")
        if type(device) is not int or not MIN_DEVICE_BYTES <= device <= 2**40:
            raise ValueError("invalid GPU memory request")
        budget = arguments.get("block_device_bytes", device)
        if type(budget) is not int or not MIB <= budget <= device:
            raise ValueError("invalid block device budget")
        if block_count(dp, budget) is None:
            raise ValueError("no block layout fits the requested GPU memory")

    def resource_requirements(self, specification):
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        resources = {"coordinator_memory_bytes": int(arguments.get("max_bytes", host_bytes(dp))),
                     "worker_memory_bytes": 0, "min_cpu_count": 1,
                     "gpu_memory_bytes": int(arguments.get("gpu_memory_bytes", MIN_DEVICE_BYTES))}
        if arguments.get("require_known_capacity"):
            resources["require_known_capacity"] = True
        return resources

    def estimate(self, specification, rate):
        dp, _, _ = decode_input(specification)
        return max(1, request_count(dp) * dp["f"]) / (rate * 20)

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        import sys
        arguments = specification["arguments"]
        command = [sys.executable, str(ROOT / "gpu_block_match_solver/cluster_solver.py"),
                   "--dp", str(output.parent / "input.khdp"), "--output", str(output),
                   "--poly", ",".join(map(str, arguments["poly"])),
                   "--threads", str(arguments.get("threads", 1)),
                   "--max-bytes", str(arguments.get("max_bytes", 2**31))]
        if "block_device_bytes" in arguments:  # smaller blocks than the leased device would allow
            command += ["--block-device-bytes", str(arguments["block_device_bytes"])]
        return command

    def runtime_files(self):
        """Block kernel and its bridge; the shared GPU, CPU matching and probe files ship with their own adapters."""
        return [
            (ROOT / "gpu_block_match_solver/__init__.py", "gpu_block_match_solver/__init__.py"),
            (ROOT / "gpu_block_match_solver/adapter.py", "gpu_block_match_solver/adapter.py"),
            (ROOT / "gpu_block_match_solver/cluster_solver.py", "gpu_block_match_solver/cluster_solver.py"),
            (ROOT / "gpu_block_match_solver/submit.py", "gpu_block_match_solver/submit.py"),
            (ROOT / "gpu_block_match_solver/kh_gpu_block_kernel", "gpu_block_match_solver/kh_gpu_block_kernel"),
        ]
