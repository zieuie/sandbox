"""Wide GPU matching (`match_gpu_wide`) for fields past the block matcher's limits, behind the cluster adapter contract.

Same shape as gpu_block_match_solver: one pinned field attempt on one fenced GPU, block by block,
no checkpoints. The kernel builds field rows in passes, so host memory is a choice, not a field
property: more memory means fewer passes (docs/GPU_WIDE_MATCHING_PLAN.md).
"""

from __future__ import annotations

from pathlib import Path

from gpu_match_solver.adapter import GPUMatchingAdapter
from matching_solver.adapter import decode_input
from matching_solver.artifacts import request_count, wide_fixed_memory, wide_row_bytes

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024**2
GIB = 1024**3
EXTRA_CELLS = 256                  # mirrors EXTRA_MAX in src/main.c
MIN_DEVICE_BYTES = 512 * MIB       # smallest GPU lease worth planning blocks on
MAX_Q = 2**40 - 1                  # FW_MAX_Q - 1 in src/field_walk.h
MAX_F = 2**32 - 2                  # choices are 32-bit on the device; all ones means unmatched
VERIFY_THREADS = 12                # kh_verify_wide on the matching host (artifacts.verify_threads)


def breakpoints(dp: dict) -> int:
    """Row breakpoints per stored cell: labels are 32-bit words plus (q-1) >> 32 breakpoints."""
    return (dp["q"] - 1) >> 32


def used_cells(dp: dict) -> int:
    return max(run["a"] for run in dp["runs"]) * dp["f"]


def all_rows_bytes(dp: dict, threads: int) -> int:
    """Rows of every used cell in one pass; mirrors fw_rows_bytes() in src/field_walk.c."""
    nbp, cells = breakpoints(dp), used_cells(dp)
    chunks = 2 * threads + nbp + 1
    return cells * (4 * (dp["f"] + nbp) + 4 * chunks) + 4 * dp["p"] * dp["f"]


def kernel_fixed_bytes(dp: dict, threads: int, device_bytes: int) -> int:
    """Everything the kernel holds besides pass rows; mirrors `fixed` in main() of src/main.c."""
    n, nbp, budget = request_count(dp), breakpoints(dp), dp["p"] * dp["f"]
    walk = 4 * (2 * threads + nbp + 1) * budget + 8 * (2 * threads + nbp + 2) + 4 * dp["p"] * dp["r"]
    pending = 12 * min(n // 1000 + 16, n)
    return walk + device_bytes // 8 + pending + 8 * MIB * threads + 256 * MIB


def minimum_host_bytes(dp: dict, threads: int, device_bytes: int) -> int:
    """Least host memory that runs at all: the kernel with rows for about 1/64 of the used cells
    per pass (far more passes than wanted), and kh_verify_wide with as many rows."""
    rows = -(-all_rows_bytes(dp, threads) // 64)
    kernel = kernel_fixed_bytes(dp, threads, device_bytes) + rows
    verifier = wide_fixed_memory(dp, dp["q"], dp["f"], VERIFY_THREADS) + rows
    return max(kernel, verifier)


def host_bytes(dp: dict, threads: int = 4, device_bytes: int = 6 * GIB, available: int | None = None) -> int:
    """Host memory to ask for: every row in one pass if that fits `available`, else all of
    `available` (more passes). Never below minimum_host_bytes()."""
    kernel = kernel_fixed_bytes(dp, threads, device_bytes) + all_rows_bytes(dp, threads)
    verifier = wide_fixed_memory(dp, dp["q"], dp["f"], VERIFY_THREADS) + \
        used_cells(dp) * wide_row_bytes(dp["q"], dp["f"], VERIFY_THREADS)
    wanted = max(kernel, verifier)
    if available is not None:
        wanted = min(wanted, available)
    return max(wanted, minimum_host_bytes(dp, threads, device_bytes))


def block_cost(dp: dict, cells: int, requests: int, window: int | None = None) -> int:
    """Device bytes for a block; mirrors block_cost() in src/main.c (4-byte choices)."""
    window = requests if window is None else window
    return (4 * (dp["f"] + breakpoints(dp)) * (cells + EXTRA_CELLS) + 35 * (requests + requests // 64 + 64) +
            4 * window + 16 * MIB)


def block_count(dp: dict, device_bytes: int) -> int | None:
    """Fewest equal blocks that fit device_bytes (an estimate: the kernel cuts on whole cells), or None.

    The last block's window also takes the spare rights (q - n), as in the kernel."""
    n, q = request_count(dp), dp["q"]
    cells = used_cells(dp)
    for count in range(1, cells + 1):
        per_block = -(-n // count)
        if per_block + q - n >= 2**32 - 4:
            continue
        if block_cost(dp, -(-cells // count), per_block, per_block + q - n) <= device_bytes:
            return count
    return None


class GPUWideMatchingAdapter(GPUMatchingAdapter):
    """Run one pinned field attempt on one fenced GPU, in row passes and blocks; hours, no checkpoints."""

    programs = ("match_gpu_wide",)
    max_q = MAX_Q
    max_requests = MAX_Q

    def validate(self, specification, internal=False):
        """Pinned-field validation with the wide kernel's bounds (32-bit choices, q < 2^40, p != 2)."""
        super(GPUMatchingAdapter, self).validate(specification, internal)
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        if dp["f"] > MAX_F or dp["p"] == 2:
            raise ValueError("wide GPU matching requires F <= 2^32 - 2 and p > 2")
        if request_count(dp) > self.max_requests:
            raise ValueError(f"wide GPU matching requires at most {self.max_requests} requests")
        device = arguments.get("gpu_memory_bytes", MIN_DEVICE_BYTES)
        if type(device) is not int or not MIN_DEVICE_BYTES <= device <= 2**40:
            raise ValueError("invalid GPU memory request")
        budget = arguments.get("block_device_bytes", device)
        if type(budget) is not int or not MIB <= budget <= device:
            raise ValueError("invalid block device budget")
        if block_count(dp, budget) is None:
            raise ValueError("no block layout fits the requested GPU memory")
        threads = int(arguments.get("threads", 1))
        if int(arguments.get("max_bytes", 0)) < minimum_host_bytes(dp, threads, device):
            raise ValueError("max_bytes is below the wide matcher's minimum for this field")

    def resource_requirements(self, specification):
        dp, _, _ = decode_input(specification)
        arguments = specification["arguments"]
        resources = {"coordinator_memory_bytes": int(arguments["max_bytes"]),
                     "worker_memory_bytes": 0, "min_cpu_count": 1,
                     "gpu_memory_bytes": int(arguments.get("gpu_memory_bytes", MIN_DEVICE_BYTES))}
        if arguments.get("require_known_capacity"):
            resources["require_known_capacity"] = True
        return resources

    def estimate(self, specification, rate):
        dp, _, _ = decode_input(specification)
        return max(1, request_count(dp) * dp["f"]) / (rate * 10)

    def command(self, specification, output, checkpoint, checkpoint_seconds):
        import sys
        arguments = specification["arguments"]
        command = [sys.executable, str(ROOT / "gpu_wide_match_solver/cluster_solver.py"),
                   "--dp", str(output.parent / "input.khdp"), "--output", str(output),
                   "--poly", ",".join(map(str, arguments["poly"])),
                   "--threads", str(arguments.get("threads", 1)),
                   "--max-bytes", str(arguments["max_bytes"])]
        if "block_device_bytes" in arguments:  # smaller blocks than the leased device would allow
            command += ["--block-device-bytes", str(arguments["block_device_bytes"])]
        if "row_bytes" in arguments:           # smaller passes than max_bytes would allow (tests)
            command += ["--row-bytes", str(arguments["row_bytes"])]
        return command

    def runtime_files(self):
        """Wide kernel, its verifier and bridge; the shared GPU, CPU matching and probe files ship with their own adapters."""
        return [
            (ROOT / "gpu_wide_match_solver/__init__.py", "gpu_wide_match_solver/__init__.py"),
            (ROOT / "gpu_wide_match_solver/adapter.py", "gpu_wide_match_solver/adapter.py"),
            (ROOT / "gpu_wide_match_solver/cluster_solver.py", "gpu_wide_match_solver/cluster_solver.py"),
            (ROOT / "gpu_wide_match_solver/submit.py", "gpu_wide_match_solver/submit.py"),
            (ROOT / "gpu_wide_match_solver/kh_gpu_wide_kernel", "gpu_wide_match_solver/kh_gpu_wide_kernel"),
            (ROOT / "gpu_wide_match_solver/kh_verify_wide", "gpu_wide_match_solver/kh_verify_wide"),
        ]
