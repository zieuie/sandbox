"""Construct pinned partitioned requests with explicit per-host admission."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cluster"))
from matching_solver.artifacts import load_dp
from matching_solver.submit import specification as single_specification
from matching_solver_multi.adapter import PartitionedAdapter
from matching_solver_multi.resources import admitted_memory


def specification(dp_path, polynomial, workers=2, threads=1, batch=65536,
                  margin=25, max_bytes=6 * 1024**3, max_edges=10**15,
                  max_field_elements=100_000_000):
    dp, _ = load_dp(dp_path)
    coordinator, owner = admitted_memory(dp, workers, threads, batch, margin)
    if max(coordinator, owner) > max_bytes:
        raise ValueError("partitioned group exceeds per-machine memory limit")
    job = single_specification(Path(dp_path), polynomial, threads, coordinator)
    job["program"] = "match_partitioned"
    job["arguments"].update(workers=workers, batch=batch, memory_margin_percent=margin,
                            owner_max_bytes=owner, coordinator_max_bytes=coordinator,
                            max_edges=max_edges, max_field_elements=max_field_elements)
    PartitionedAdapter().validate(job)
    return job
