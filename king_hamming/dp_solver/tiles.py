"""DP tile geometry, dependency planning and bounded native predecessor assembly."""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dp_solver.scheduling import dp_estimate


# Immutable global rectangle and the exact predecessor coordinate envelope.
@dataclass(frozen=True)
class tile_t:
    """Describe one nonempty tile of positive-budget DP cells."""

    row: int
    column: int
    first_u: int
    last_u: int
    first_v: int
    last_v: int
    origin_u: int
    origin_v: int

    @property
    def value_bytes(self) -> int:
        """Return tile-only uint64 value bytes."""

        return (self.last_u - self.first_u + 1) * (self.last_v - self.first_v + 1) * 8

    @property
    def halo_bytes(self) -> int:
        """Return the rectangular native input image size including the interior."""

        return (self.last_u - self.origin_u + 1) * (self.last_v - self.origin_v + 1) * 8


# Bound every transition's coordinate cost by p squared, clipping against the zero axes.
def tile(p: int, r: int, side: int, row: int, column: int) -> tile_t:
    """Return validated tile coordinates for p^r and zero-based grid indices."""

    budget = dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})["budget"]

    if type(side) is not int or side < 1 or min(row, column) < 0:
        raise ValueError("invalid tile side or grid coordinate")
    first_u = row * side + 1
    first_v = column * side + 1

    if max(first_u, first_v) > budget:
        raise ValueError("tile is outside the DP budget")

    return tile_t(row, column, first_u, min(first_u + side - 1, budget),
                  first_v, min(first_v + side - 1, budget), max(0, first_u - p*p), max(0, first_v - p*p))


# List a conservative predecessor cover without requesting any future or same-wave tile.
def dependencies(p: int, r: int, side: int, target: tile_t) -> list[tile_t]:
    """Return all earlier tiles intersecting target's halo; current interior is excluded."""

    first_row = max(0, (max(1, target.origin_u) - 1) // side)
    first_column = max(0, (max(1, target.origin_v) - 1) // side)
    return [tile(p, r, side, row, column)
            for row in range(first_row, target.row + 1)
            for column in range(first_column, target.column + 1)
            if (row, column) != (target.row, target.column)]


# Assemble from immutable predecessor tile files with bounded read buffers and sparse zero axes.
def build_halo(
    p: int, r: int, side: int, target: tile_t, sources: dict[tuple[int, int], Path],
    output: Path, max_bytes: int = 2 * 1024**3, check: Callable[[], None] | None = None,
) -> None:
    """Write exact halo input to fresh output using validated predecessor sizes and max_bytes."""

    expected = dependencies(p, r, side, target)

    if target.halo_bytes > max_bytes:
        raise ValueError("halo exceeds byte admission")
    for predecessor in expected:
        path = sources[(predecessor.row, predecessor.column)]
        if path.stat().st_size != predecessor.value_bytes:
            raise ValueError("predecessor tile has incorrect value size")

    with output.open("xb") as destination:
        destination.truncate(target.halo_bytes)
        width = target.last_v - target.origin_v + 1

        for predecessor in expected:
            low_u = max(target.origin_u, predecessor.first_u)
            high_u = min(target.last_u, predecessor.last_u)
            low_v = max(target.origin_v, predecessor.first_v)
            high_v = min(target.last_v, predecessor.last_v)
            source_width = predecessor.last_v - predecessor.first_v + 1

            with sources[(predecessor.row, predecessor.column)].open("rb") as source:
                for u in range(low_u, high_u + 1):
                    source.seek(((u - predecessor.first_u) * source_width + low_v - predecessor.first_v) * 8)
                    destination.seek(((u - target.origin_u) * width + low_v - target.origin_v) * 8)
                    remaining = (high_v - low_v + 1) * 8

                    while remaining:
                        if check is not None:
                            check()
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise OSError("predecessor tile truncated during assembly")
                        destination.write(block)
                        remaining -= len(block)


# Use the same conservative admission formula as the native tile kernel.
def memory_bytes(p: int, target: tile_t, threads: int) -> int:
    """Return halo, choices, sort reserve, stack reserve and fixed overhead for this task."""

    if type(threads) is not int or threads < 1:
        raise ValueError("threads must be positive")
    return target.halo_bytes + target.value_bytes//2 + p**3*24 + threads*8*1024**2 + 64*1024**2
