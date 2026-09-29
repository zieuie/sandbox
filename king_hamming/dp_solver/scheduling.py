"""Checked workload estimates and deterministic prime-power campaign generation."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import math
from typing import Any, Iterator

UINT32_MAX = 2**32 - 1
DEFAULT_STATE_BYTES = 16 * 1024**3
DEFAULT_VISITS = 5_000_000_000


# Test the small base characteristic without external libraries or probabilistic choices.
def is_prime(value: int) -> bool:
    """Return whether integer value is prime using trial division."""

    if type(value) is not int or value < 2:
        return False

    return all(value % divisor for divisor in range(2, math.isqrt(value) + 1))


# Derive the same dense-state and conservative recurrence costs used by the C prototype.
def dp_estimate(specification: dict[str, Any]) -> dict[str, int]:
    """Return checked field, state and raw-visit estimates; reject unsupported parameters."""

    arguments = specification.get("arguments", {})
    p = arguments["p"]
    r = arguments["r"]

    if not is_prime(p) or p > 1621 or type(r) is not int or r < 3 or r > 31 or r % 2 == 0:
        raise ValueError("DP requires prime p and odd r between 3 and 31")

    q = p**r

    if q > UINT32_MAX:
        raise ValueError("field size exceeds unsigned 32-bit limit")

    budget = p**((r + 1) // 2)
    return {"q": q, "budget": budget, "state_bytes": (budget + 1)**2 * 12,
            "transition_bytes": p**3 * 12, "raw_visits": budget**2 * p**3}


# Produce a bounded frontier of all supported odd-degree fields, ordered by estimated work.
def campaign(
    max_state_bytes: int = DEFAULT_STATE_BYTES, max_visits: int = DEFAULT_VISITS,
    threads: int = 1, tile_side: int = 4096,
) -> Iterator[dict[str, Any]]:
    """Yield admitted DP specifications in raw-visit order with deterministic field ties."""

    limits = (max_state_bytes, max_visits, threads, tile_side)
    if any(type(value) is not int or value <= 0 for value in limits):
        raise ValueError("campaign limits and worker parameters must be positive integers")
    if max(max_state_bytes, max_visits) > 2**64 - 1 or max(threads, tile_side) > UINT32_MAX:
        raise ValueError("campaign argument exceeds its solver integer width")

    candidates = []

    for p in range(2, 1622):
        if not is_prime(p):
            continue

        for r in range(3, 32, 2):
            if p**r > UINT32_MAX:
                break

            specification = {"program": "dp", "arguments": {
                "p": p, "r": r, "threads": threads, "tile_side": tile_side,
                "max_state_bytes": max_state_bytes, "max_visits": max_visits,
            }}
            estimate = dp_estimate(specification)

            if estimate["state_bytes"] <= max_state_bytes and estimate["raw_visits"] <= max_visits:
                candidates.append((estimate["raw_visits"], estimate["q"], p, r, specification))

    for _, _, _, _, specification in sorted(candidates):
        yield specification
