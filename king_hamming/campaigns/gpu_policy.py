"""Route matchable fields to a single fenced GPU (match_gpu) when any live node can hold them."""

from __future__ import annotations

from matching_solver.artifacts import request_count
from gpu_match_solver.adapter import device_bytes, host_bytes
import gpus
from resources import HOST_RESERVE_BYTES

DEFAULTS = {"gpu_matching": True, "gpu_matching_threads": 4}


def plan(dp: dict, settings: dict, nodes: list[dict]) -> dict | None:
    """Return an admitted match_gpu plan, or None when no live GPU can hold this field."""
    if not settings.get("gpu_matching", DEFAULTS["gpu_matching"]):
        return None
    if dp["f"] > 65535 or dp["q"] > 2**32 - 1 or request_count(dp) >= 2**32 - 1:
        return None
    threads = int(settings.get("gpu_matching_threads", DEFAULTS["gpu_matching_threads"]))
    device, host = device_bytes(dp), host_bytes(dp, threads)
    hosts = []
    for node in nodes:
        if node.get("state", "healthy") != "healthy" or not node.get("compute_enabled", True):
            continue
        ram = int(node.get("memory_bytes") or 0)
        if ram and ram - HOST_RESERVE_BYTES < host:
            continue
        if any(gpus.usable_bytes(item) >= device for item in gpus.from_record(node)):
            hosts.append(node["node_name"])
    if not hosts:
        return None
    return dict(admitted=True, reason="admitted: single GPU", program="match_gpu", workers=1,
                threads=threads, max_bytes=host, gpu_memory_bytes=device, hosts=sorted(hosts))
