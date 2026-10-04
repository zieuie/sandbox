"""Route matchable fields to one fenced GPU: whole (match_gpu), or block by block (match_gpu_blocks) when too big."""

from __future__ import annotations

from matching_solver.artifacts import request_count
from gpu_match_solver.adapter import device_bytes, host_bytes
from gpu_block_match_solver import adapter as block_adapter
import gpus
from resources import HOST_RESERVE_BYTES

DEFAULTS = {"gpu_matching": True, "gpu_matching_threads": 4, "gpu_block_matching": True}


def plan(dp: dict, settings: dict, nodes: list[dict]) -> dict | None:
    """Return an admitted match_gpu plan, or None when no live GPU can hold this field."""
    if not settings.get("gpu_matching", DEFAULTS["gpu_matching"]):
        return None
    threads = int(settings.get("gpu_matching_threads", DEFAULTS["gpu_matching_threads"]))
    if dp["f"] > 65535 or dp["q"] > 2**32 - 1 or request_count(dp) >= 2**32 - 1:
        # Only block mode takes fields this large (64-bit labels, rows of used cells only).
        return block_plan(dp, settings, nodes, threads)
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
        return block_plan(dp, settings, nodes, threads)
    return dict(admitted=True, reason="admitted: single GPU", program="match_gpu", workers=1,
                threads=threads, max_bytes=host, gpu_memory_bytes=device, hosts=sorted(hosts))


def block_plan(dp: dict, settings: dict, nodes: list[dict], threads: int) -> dict | None:
    """Plan match_gpu_blocks on every host that has the RAM for it; None if none does.

    Used only when no GPU holds the whole field. Any machine with a usable GPU and enough
    host memory may take the run, so several fields match at once. The lease asks for the
    smallest eligible device; the kernel sizes its blocks from whatever device it gets, so a
    bigger GPU simply uses fewer blocks.
    """
    if not settings.get("gpu_block_matching", DEFAULTS["gpu_block_matching"]) or dp["f"] > 65534:
        return None
    if dp["q"] > block_adapter.MAX_Q or request_count(dp) > block_adapter.MAX_Q:
        return None
    host = block_adapter.host_bytes(dp, threads)
    hosts = []
    for node in nodes:
        if node.get("state", "healthy") != "healthy" or not node.get("compute_enabled", True):
            continue
        ram = int(node.get("memory_bytes") or 0)
        if ram and ram - HOST_RESERVE_BYTES < host:
            continue
        usable = max([gpus.usable_bytes(item) for item in gpus.from_record(node)] or [0])
        if usable >= block_adapter.MIN_DEVICE_BYTES and block_adapter.block_count(dp, usable) is not None:
            hosts.append((usable, node["node_name"]))
    if not hosts:
        return None
    smallest = min(usable for usable, _ in hosts)
    hosts = sorted(name for _, name in hosts)
    return dict(admitted=True, reason="admitted: GPU blocks", program="match_gpu_blocks", workers=1,
                threads=threads, max_bytes=host, gpu_memory_bytes=smallest, hosts=hosts,
                blocks=block_adapter.block_count(dp, smallest))
