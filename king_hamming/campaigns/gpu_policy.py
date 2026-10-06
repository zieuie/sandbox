"""Route matchable fields to one fenced GPU: whole (match_gpu), block by block (match_gpu_blocks) when
too big, or in row passes (match_gpu_wide) past the block matcher's limits."""

from __future__ import annotations

from matching_solver.artifacts import request_count
from gpu_match_solver.adapter import device_bytes, host_bytes
from gpu_block_match_solver import adapter as block_adapter
from gpu_wide_match_solver import adapter as wide_adapter
import gpus
from resources import HOST_RESERVE_BYTES

DEFAULTS = {"gpu_matching": True, "gpu_matching_threads": 4, "gpu_block_matching": True,
            "gpu_wide_matching": True}
# Share of a host's memory (after the system reserve) a wide run may take: it would happily use
# all of it for fewer row passes, and merlin also runs the leader, dashboard and feeder.
WIDE_HOST_FRACTION = 0.75
DEFAULT_MINIMUM_FREE_BYTES = 20 * 1024**3   # the feeder's minimum_free_bytes default


def certificate_bytes(dp: dict) -> int:
    """Size of a full matching's KHM1 certificate: packed choices, a header and the checksum."""
    n = request_count(dp)
    return (n * (dp["f"] - 1).bit_length() + 7) // 8 + 4096


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
        return wide_plan(dp, settings, nodes, threads)
    if dp["q"] > block_adapter.MAX_Q or request_count(dp) > block_adapter.MAX_Q:
        return wide_plan(dp, settings, nodes, threads)
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
        return wide_plan(dp, settings, nodes, threads)
    smallest = min(usable for usable, _ in hosts)
    hosts = sorted(name for _, name in hosts)
    return dict(admitted=True, reason="admitted: GPU blocks", program="match_gpu_blocks", workers=1,
                threads=threads, max_bytes=host, gpu_memory_bytes=smallest, hosts=hosts,
                blocks=block_adapter.block_count(dp, smallest))


def wide_usable(node: dict) -> int:
    """Host bytes a wide run may plan on this node: a share of its memory after the reserve."""
    ram = int(node.get("memory_bytes") or 0)
    return int((ram - HOST_RESERVE_BYTES) * WIDE_HOST_FRACTION) if ram > HOST_RESERVE_BYTES else 0


def wide_plan(dp: dict, settings: dict, nodes: list[dict], threads: int) -> dict | None:
    """Plan match_gpu_wide when the block matcher can't take the field (F, q, or rows in memory).

    Of the healthy machines with a usable GPU, memory for the wide matcher's minimum and disk for
    the certificate, only those with the largest GPU are offered. The run takes up to
    WIDE_HOST_FRACTION of the smallest of those hosts' memory: more memory only means fewer row
    passes, in the solver and in kh_verify_wide.
    """
    if not settings.get("gpu_wide_matching", DEFAULTS["gpu_wide_matching"]):
        return None
    if dp["p"] == 2 or dp["f"] > wide_adapter.MAX_F or dp["q"] > wide_adapter.MAX_Q:
        return None
    hosts = []
    for node in nodes:
        if node.get("state", "healthy") != "healthy" or not node.get("compute_enabled", True):
            continue
        usable = max([gpus.usable_bytes(item) for item in gpus.from_record(node)] or [0])
        if usable < wide_adapter.MIN_DEVICE_BYTES or wide_adapter.block_count(dp, usable) is None:
            continue
        if wide_usable(node) < wide_adapter.minimum_host_bytes(dp, threads, usable):
            continue
        # The certificate is written in place on this host's disk (206 GB for 7^13): it must fit
        # with the feeder's free-space floor to spare, or the run would fill the disk mid-way.
        if int(node.get("storage_free_bytes") or 0) < certificate_bytes(dp) + int(
                settings.get("minimum_free_bytes", DEFAULT_MINIMUM_FREE_BYTES)):
            continue
        hosts.append((usable, wide_usable(node), node["node_name"]))
    if not hosts:
        return None
    # A wide run takes hours and its time is set by the GPU and the passes, so it goes only to the
    # machines with the largest GPU (merlin's 3060, not a P600 holding 743 small blocks), sized
    # for them. The block matcher instead offers any machine with the memory: its runs are short.
    largest = max(usable for usable, _, _ in hosts)
    hosts = [host for host in hosts if host[0] == largest]
    device = largest
    available = min(memory for _, memory, _ in hosts)
    host = wide_adapter.host_bytes(dp, threads, device, available)
    return dict(admitted=True, reason="admitted: GPU wide", program="match_gpu_wide", workers=1,
                threads=threads, max_bytes=host, gpu_memory_bytes=device,
                hosts=sorted(name for _, _, name in hosts), blocks=wide_adapter.block_count(dp, device))


def gib(value: int) -> str:
    return f"{value / 1024**3:.1f} GiB"


def blocker(dp: dict, settings: dict, nodes: list[dict]) -> str | None:
    """Say why plan() found no GPU for this field, or None when it did or GPU matching is off.

    The dashboard shows this next to a field the CPU limits refuse. It names every limit that
    binds: the block matcher's (F, q, host RAM), then why the wide matcher can't take it either.
    """
    if not settings.get("gpu_matching", DEFAULTS["gpu_matching"]) or plan(dp, settings, nodes) is not None:
        return None
    q, n, f = dp["q"], request_count(dp), dp["f"]
    machines = [node for node in nodes
                if node.get("state", "healthy") == "healthy" and node.get("compute_enabled", True)
                and gpus.from_record(node)]
    if not machines:
        return "no machine with a GPU is online"
    threads = int(settings.get("gpu_matching_threads", DEFAULTS["gpu_matching_threads"]))
    largest = max(int(node.get("memory_bytes") or 0) for node in machines)
    limits = []
    if not settings.get("gpu_block_matching", DEFAULTS["gpu_block_matching"]):
        limits.append("block matching is turned off")
    else:
        if f > 65534:
            limits.append(f"F = {f:,} is above the block matcher's 65,534")
        if q > block_adapter.MAX_Q or n > block_adapter.MAX_Q:
            limits.append(f"q = {q:,} is above the block matcher's {block_adapter.MAX_Q:,}")
        need = block_adapter.host_bytes(dp, threads)
        if largest - HOST_RESERVE_BYTES < need:
            limits.append(f"block matching needs {gib(need)} of host RAM (largest GPU machine: {gib(largest)}, "
                          f"less {gib(HOST_RESERVE_BYTES)} kept for the system)")
    if not settings.get("gpu_wide_matching", DEFAULTS["gpu_wide_matching"]):
        limits.append("wide matching is turned off")
    elif dp["p"] == 2:
        limits.append("the wide matcher refuses p = 2 (F is a power of two)")
    elif q > wide_adapter.MAX_Q:
        limits.append(f"q = {q:,} is above the wide matcher's {wide_adapter.MAX_Q:,}")
    else:
        floor = int(settings.get("minimum_free_bytes", DEFAULT_MINIMUM_FREE_BYTES))
        fitting = []   # machines whose GPU takes a block
        for node in machines:
            device = max([gpus.usable_bytes(item) for item in gpus.from_record(node)] or [0])
            if device >= wide_adapter.MIN_DEVICE_BYTES and wide_adapter.block_count(dp, device) is not None:
                fitting.append((node, device))
        roomy = [node for node, device in fitting
                 if wide_usable(node) >= wide_adapter.minimum_host_bytes(dp, threads, device)]
        if not fitting:
            limits.append("no GPU has room for even one block of this field")
        elif not roomy:
            need = min(wide_adapter.minimum_host_bytes(dp, threads, device) for _, device in fitting)
            usable = max(wide_usable(node) for node, _ in fitting)
            limits.append(f"wide matching needs at least {gib(need)} of host RAM; the largest GPU machine "
                          f"allows {gib(usable)}")
        else:
            disk = max(int(node.get("storage_free_bytes") or 0) for node in roomy)
            limits.append(f"wide matching writes a {gib(certificate_bytes(dp))} certificate; the GPU machine "
                          f"with the memory for it has {gib(disk)} of free disk, and {gib(floor)} must stay free")
    return "; ".join(limits)
