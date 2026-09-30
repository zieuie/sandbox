"""Solver-neutral compute-capacity and admission arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


HOST_RESERVE_BYTES = 2 * 1024**3


def cpu_count(cpu_set: str) -> int:
    """Return the number of explicit logical CPUs in an agent registration."""

    return len({item.strip() for item in cpu_set.split(",") if item.strip()})


def normalized_slots(raw: Any, host_cpu_set: str) -> list[dict[str, Any]]:
    """Validate disjoint registered CPU slots, falling back to one whole-host slot."""

    host = [item.strip() for item in host_cpu_set.split(",") if item.strip()]
    if raw is None:
        return [{"slot_id": 0, "cpu_set": ",".join(host)}]
    if not isinstance(raw, list) or not raw or len(raw) > 256:
        raise ValueError("invalid compute slots")
    allowed, seen, identifiers, result = set(host), set(), set(), []
    for item in raw:
        if not isinstance(item, dict) or type(item.get("slot_id")) is not int:
            raise ValueError("invalid compute slot")
        slot_id = item["slot_id"]
        cpus = [value.strip() for value in str(item.get("cpu_set", "")).split(",")
                if value.strip()]
        unknown_legacy = not host and len(raw) == 1 and not cpus
        if (slot_id < 0 or slot_id in identifiers or
                (not cpus and not unknown_legacy) or len(cpus) != len(set(cpus))):
            raise ValueError("invalid compute slot identity")
        if any(cpu not in allowed or cpu in seen for cpu in cpus):
            raise ValueError("compute slots must use disjoint registered CPUs")
        identifiers.add(slot_id)
        seen.update(cpus)
        result.append({"slot_id": slot_id, "cpu_set": ",".join(cpus)})
    return sorted(result, key=lambda item: item["slot_id"])


@dataclass(frozen=True)
class ResourceRequest:
    """Validated per-host resources requested by one solver lease."""

    coordinator_memory_bytes: int
    worker_memory_bytes: int
    min_cpu_count: int

    @classmethod
    def from_adapter(cls, raw: Any) -> "ResourceRequest":
        """Validate an adapter response and return its typed representation."""

        keys = ("coordinator_memory_bytes", "worker_memory_bytes", "min_cpu_count")
        if (not isinstance(raw, dict) or
                any(type(raw.get(key)) is not int or raw[key] < 0 for key in keys) or
                raw["min_cpu_count"] < 1):
            raise ValueError("adapter requested invalid node resources")
        return cls(*(raw[key] for key in keys))

    def memory_for(self, role: str) -> int:
        """Return the host-memory request for coordinator or worker role."""

        if role == "coordinator":
            return self.coordinator_memory_bytes
        if role == "worker":
            return self.worker_memory_bytes
        raise ValueError(f"unknown resource role: {role}")


@dataclass(frozen=True)
class NodeCapacity:
    """Normalized capacity advertised by one agent."""

    cpu_count: int
    memory_bytes: int

    @classmethod
    def from_record(cls, node: Mapping[str, Any]) -> "NodeCapacity":
        """Normalize one SQLite/status node record without trusting truthiness."""

        keys = node.keys()
        cpu_set = node["cpu_set"] if "cpu_set" in keys else ""
        memory = node["memory_bytes"] if "memory_bytes" in keys else 0
        return cls(cpu_count(str(cpu_set)), max(0, int(memory)))

    @property
    def usable_memory_bytes(self) -> int:
        """Retain operating-system headroom on nodes that report memory."""

        return max(0, self.memory_bytes - HOST_RESERVE_BYTES)

    def fits(self, request: ResourceRequest, role: str) -> bool:
        """Return whether one lease in role fits this node.

        A zero advertised CPU or memory value is retained as the legacy
        "unknown, do not reject" convention.
        """

        memory = request.memory_for(role)
        return ((self.cpu_count == 0 or self.cpu_count >= request.min_cpu_count) and
                (self.memory_bytes == 0 or self.usable_memory_bytes >= memory))

    def safe_slots(self, request: ResourceRequest, role: str) -> int:
        """Return an upper bound on disjoint equal-sized leases for planning.

        This does not authorize concurrent leases. The scheduler separately
        fences disjoint CPU teams and aggregate host memory for each lease.
        """

        if not self.fits(request, role):
            return 0
        cpu_slots = (self.cpu_count // request.min_cpu_count
                     if self.cpu_count else 1)
        memory = request.memory_for(role)
        memory_slots = (self.usable_memory_bytes // memory
                        if self.memory_bytes and memory else cpu_slots)
        return max(1, min(cpu_slots, memory_slots))


def fits(node: Mapping[str, Any], request: ResourceRequest, role: str) -> bool:
    """Convenience wrapper for scheduler call sites."""

    return NodeCapacity.from_record(node).fits(request, role)
