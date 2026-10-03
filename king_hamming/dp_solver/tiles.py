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


# A rectangle of global cells stored row-major as uint64 values.
@dataclass(frozen=True)
class region_t:
    """Name the global cells held by one values file: a whole tile or one edge band."""

    first_u: int
    last_u: int
    first_v: int
    last_v: int

    @property
    def width(self) -> int:
        """Return the number of columns."""

        return self.last_v - self.first_v + 1

    @property
    def value_bytes(self) -> int:
        """Return the row-major uint64 byte size of the rectangle."""

        return (self.last_u - self.first_u + 1) * self.width * 8

    def covers(self, low_u: int, high_u: int, low_v: int, high_v: int) -> bool:
        """Return whether every cell of the given rectangle lies inside this region."""

        return (self.first_u <= low_u and high_u <= self.last_u and
                self.first_v <= low_v and high_v <= self.last_v)


# A values file together with the cells it holds; legacy tile packets hold the whole tile.
@dataclass(frozen=True)
class piece_t:
    """Pair a region with the raw uint64 file that stores it."""

    region: region_t
    path: Path


def whole(predecessor: tile_t) -> region_t:
    """Return the region covered by a complete tile values file."""

    return region_t(predecessor.first_u, predecessor.last_u, predecessor.first_v, predecessor.last_v)


BAND_KINDS = ("bottom", "right", "corner")


# A successor's halo reaches at most p squared cells into each predecessor, from one side or corner.
def band_kind(target: tile_t, predecessor: tile_t) -> str:
    """Name the edge band of predecessor that covers its intersection with target's halo.

    A predecessor in the same tile column lies above, so only its bottom rows
    are reached; one in the same tile row lies to the left, so only its right
    columns; any other predecessor is diagonal and contributes just its corner.
    """

    if predecessor.column == target.column:
        return "bottom"
    if predecessor.row == target.row:
        return "right"
    return "corner"


def band_region(p: int, rectangle: tile_t, kind: str) -> region_t:
    """Return the cells of rectangle held by its kind band, p squared thick and clipped to the tile."""

    if kind not in BAND_KINDS:
        raise ValueError("unknown tile band kind")
    thickness = p * p
    first_u = max(rectangle.first_u, rectangle.last_u - thickness + 1) if kind != "right" else rectangle.first_u
    first_v = max(rectangle.first_v, rectangle.last_v - thickness + 1) if kind != "bottom" else rectangle.first_v
    return region_t(first_u, rectangle.last_u, first_v, rectangle.last_v)


# Edge tiles have no successor below or beside them, so their unused bands are never published.
def needed_bands(p: int, r: int, side: int, rectangle: tile_t) -> tuple[str, ...]:
    """Return the band kinds some later tile can use."""

    budget = dp_estimate({"program": "dp", "arguments": {"p": p, "r": r}})["budget"]
    count = (budget + side - 1) // side
    below = rectangle.row + 1 < count
    beside = rectangle.column + 1 < count
    return tuple(kind for kind, wanted in (("bottom", below), ("right", beside), ("corner", below and beside))
                 if wanted)


# Copy only the band's rows and columns, in bounded reads, so a band never needs the whole tile in memory.
def extract_region(values: Path, rectangle: tile_t, region: region_t, output,
                   check: Callable[[], None] | None = None) -> None:
    """Write region's cells, row-major, from a complete tile values file to the binary stream output."""

    for row in region_rows(values, rectangle, region, check):
        output.write(row)


def region_rows(values: Path, rectangle: tile_t, region: region_t,
                check: Callable[[], None] | None = None):
    """Yield region's rows of native uint64 values, in order, from a complete tile values file."""

    if not whole(rectangle).covers(region.first_u, region.last_u, region.first_v, region.last_v):
        raise ValueError("band lies outside its tile")
    if values.stat().st_size != rectangle.value_bytes:
        raise ValueError("tile values have incorrect size")
    tile_width = rectangle.last_v - rectangle.first_v + 1
    with values.open("rb") as source:
        for u in range(region.first_u, region.last_u + 1):
            if check is not None:
                check()
            source.seek(((u - rectangle.first_u) * tile_width + region.first_v - rectangle.first_v) * 8)
            row = source.read(region.width * 8)
            if len(row) != region.width * 8:
                raise OSError("tile values truncated during band extraction")
            yield row


# Assemble from immutable predecessor values (whole tiles or edge bands) with bounded buffers and sparse zero axes.
def build_halo(
    p: int, r: int, side: int, target: tile_t, sources: dict[tuple[int, int], Path | piece_t],
    output: Path, max_bytes: int = 2 * 1024**3, check: Callable[[], None] | None = None,
) -> None:
    """Write exact halo input to fresh output using validated predecessor sizes and max_bytes.

    A bare Path is a complete tile values file. A piece_t may hold just the band
    that covers the halo's intersection; a piece missing any needed cell is refused.
    """

    expected = dependencies(p, r, side, target)

    if target.halo_bytes > max_bytes:
        raise ValueError("halo exceeds byte admission")
    pieces = {}
    for predecessor in expected:
        source = sources[(predecessor.row, predecessor.column)]
        piece = source if isinstance(source, piece_t) else piece_t(whole(predecessor), source)
        if piece.path.stat().st_size != piece.region.value_bytes:
            raise ValueError("predecessor tile has incorrect value size")
        low_u = max(target.origin_u, predecessor.first_u)
        high_u = min(target.last_u, predecessor.last_u)
        low_v = max(target.origin_v, predecessor.first_v)
        high_v = min(target.last_v, predecessor.last_v)
        if low_u <= high_u and low_v <= high_v and not piece.region.covers(low_u, high_u, low_v, high_v):
            raise ValueError("predecessor values do not cover the halo")
        pieces[(predecessor.row, predecessor.column)] = piece

    with output.open("xb") as destination:
        destination.truncate(target.halo_bytes)
        width = target.last_v - target.origin_v + 1

        for predecessor in expected:
            piece = pieces[(predecessor.row, predecessor.column)]
            low_u = max(target.origin_u, predecessor.first_u)
            high_u = min(target.last_u, predecessor.last_u)
            low_v = max(target.origin_v, predecessor.first_v)
            high_v = min(target.last_v, predecessor.last_v)
            source_width = piece.region.width

            with piece.path.open("rb") as source:
                for u in range(low_u, high_u + 1):
                    source.seek(((u - piece.region.first_u) * source_width + low_v - piece.region.first_v) * 8)
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
