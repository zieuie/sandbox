#!/usr/bin/env python3
"""Prototype: an exact tiled DP that keeps only edge bands, with parallel reconstruction.

Experiment for docs/DP_STORAGE.md ("Keeping only the frontier"); not used by the cluster.

Forward pass, in antidiagonal waves: build each tile's halo from its predecessors'
bands, run kh_dp_tile, then keep only the tile's bands (the last p^2 rows and
columns, which are all a successor can read) plus, for each band cell x, the exit
pointer J(x): the first cell outside the tile on x's backward path, or None if the
path ends inside it. The tile's full values and choices are deleted at once.

Reconstruction: a path leaves a tile into that tile's halo, which lies in a
predecessor's band, so (B,B) -> J -> J -> ... lists every tile the optimal path
crosses and the cell where it enters each one, from band data alone. Each crossed
tile is then recomputed from its predecessors' bands and its segment traced; the
segments are independent (here they run one after another). Every transition
moves at least one step in both budgets, so J(x) depends only on cells already
visited in row-major order.

Pure Python J and tracing: suitable for small fields only. Example:
    ./frontier_prototype.py 3 7 --tile-side 16 --work-dir /tmp/frontier-3-7
"""

from __future__ import annotations

if __package__ in {None, ""}:
    import bootstrap
else:
    from . import bootstrap

import argparse
from array import array
import json
from pathlib import Path
import shutil
import subprocess
import sys

from dp_solver.scheduling import dp_estimate
from dp_solver.tiles import (band_kind, band_region, build_halo, dependencies, extract_region,
                             needed_bands, piece_t, tile, tile_t)

KERNEL = Path(__file__).resolve().parent / "kh_dp_tile"


def decode_choice(choice: int, p: int) -> tuple[int, int, int]:
    """Return (a, b, t) for a nonzero 1-based choice ID, as reconstruct() decodes it."""

    index = choice - 1
    t = index % p + 1
    index //= p
    b = index % p + 1
    return index // p + 1, b, t


def run_kernel(p: int, r: int, target: tile_t, halo: Path, output: Path) -> None:
    """Compute one tile with the production C kernel."""

    subprocess.run([str(KERNEL), str(p), str(r), str(target.first_u), str(target.last_u),
                    str(target.first_v), str(target.last_v), str(halo), str(output), "1"],
                   check=True, capture_output=True)


def read_array(path: Path, code: str) -> array:
    values = array(code)
    values.frombytes(path.read_bytes())
    return values


def exit_pointers(p: int, target: tile_t, choices: array) -> dict[tuple[int, int], tuple[int, int] | None]:
    """Return J for every cell of target: first cell outside it on the backward path, or None."""

    width = target.last_v - target.first_v + 1
    pointers: dict[tuple[int, int], tuple[int, int] | None] = {}
    for u in range(target.first_u, target.last_u + 1):
        for v in range(target.first_v, target.last_v + 1):
            choice = choices[(u - target.first_u) * width + v - target.first_v]
            if choice == 0:
                pointers[(u, v)] = None
                continue
            a, b, t = decode_choice(choice, p)
            y = (u - a * t, v - b * t)
            inside = target.first_u <= y[0] and target.first_v <= y[1]
            pointers[(u, v)] = pointers[y] if inside else y
    return pointers


class BandStore:
    """Everything the prototype keeps: band values (one raw file per tile and kind) and band J."""

    def __init__(self, root: Path, p: int, r: int, side: int) -> None:
        self.root, self.p, self.r, self.side = root, p, r, side
        self.pointers: dict[tuple[int, int], tuple[int, int] | None] = {}
        self.band_cells = 0

    def path(self, row: int, column: int, kind: str) -> Path:
        return self.root / f"band-{row}-{column}-{kind}.bin"

    def keep(self, target: tile_t, values: Path, pointers: dict) -> None:
        """Store target's needed bands and the exit pointers of their cells."""

        for kind in needed_bands(self.p, self.r, self.side, target):
            region = band_region(self.p, target, kind)
            with self.path(target.row, target.column, kind).open("xb") as output:
                extract_region(values, target, region, output)
            self.band_cells += (region.last_u - region.first_u + 1) * region.width
            for u in range(region.first_u, region.last_u + 1):
                for v in range(region.first_v, region.last_v + 1):
                    self.pointers[(u, v)] = pointers[(u, v)]

    def halo_sources(self, target: tile_t) -> dict[tuple[int, int], piece_t]:
        """Return the band piece of each predecessor that covers target's halo."""

        sources = {}
        for predecessor in dependencies(self.p, self.r, self.side, target):
            kind = band_kind(target, predecessor)
            sources[(predecessor.row, predecessor.column)] = piece_t(
                band_region(self.p, predecessor, kind), self.path(predecessor.row, predecessor.column, kind))
        return sources

    def compute(self, target: tile_t, scratch: Path) -> tuple[array, array]:
        """Recompute target exactly from stored bands; return its (values, choices)."""

        if scratch.exists():
            shutil.rmtree(scratch)
        scratch.mkdir(parents=True)
        halo = scratch / "halo.bin"
        build_halo(self.p, self.r, self.side, target, self.halo_sources(target), halo)
        run_kernel(self.p, self.r, target, halo, scratch / "out")
        return read_array(scratch / "out" / "values.bin", "Q"), read_array(scratch / "out" / "choices.bin", "I")


def solve(p: int, r: int, side: int, work: Path) -> tuple[dict, dict]:
    """Return (the KHDP2-draft split document, storage statistics) using band-only storage."""

    estimate = dp_estimate({"arguments": {"p": p, "r": r}})
    budget = estimate["budget"]
    count = (budget + side - 1) // side
    work.mkdir(parents=True, exist_ok=False)
    store = BandStore(work, p, r, side)
    final_pointer = None
    peak_tiles = 0
    for wave in range(2 * count - 1):
        live = 0
        for row in range(max(0, wave - count + 1), min(wave, count - 1) + 1):
            target = tile(p, r, side, row, wave - row)
            values, choices = store.compute(target, work / "scratch")
            pointers = exit_pointers(p, target, choices)
            store.keep(target, work / "scratch" / "out" / "values.bin", pointers)
            if (target.last_u, target.last_v) == (budget, budget):
                final_pointer = pointers[(budget, budget)]
            live += 1
        peak_tiles = max(peak_tiles, live)
    shutil.rmtree(work / "scratch")

    # The chain of tile entries, from band data alone.
    entries = [(budget, budget)]
    following = final_pointer
    while following is not None and following[0] > 0 and following[1] > 0:
        entries.append(following)
        following = store.pointers[following]

    # Each segment is independent: recompute its tile and trace until the path leaves it.
    theta = None
    runs: list[dict] = []
    for index, (u, v) in enumerate(entries):
        target = tile(p, r, side, (u - 1) // side, (v - 1) // side)
        values, choices = store.compute(target, work / f"segment-{index}")
        width = target.last_v - target.first_v + 1
        if theta is None:
            theta = values[(u - target.first_u) * width + v - target.first_v]
        while u and v and target.first_u <= u and target.first_v <= v:
            choice = choices[(u - target.first_u) * width + v - target.first_v]
            if choice == 0:
                u = v = 0
                break
            a, b, t = decode_choice(choice, p)
            if runs and (runs[-1]["a"], runs[-1]["b"], runs[-1]["t"]) == (a, b, t):
                runs[-1]["repeat"] += 1
            else:
                runs.append({"a": a, "b": b, "t": t, "repeat": 1})
            u, v = u - a * t, v - b * t
        expected = entries[index + 1] if index + 1 < len(entries) else None
        if expected is not None and (u, v) != expected:
            raise AssertionError(f"segment {index} left its tile at {(u, v)}, chain says {expected}")
        shutil.rmtree(work / f"segment-{index}")
    document = {"format": "KHDP2-draft", "p": p, "r": r, "q": estimate["q"], "f": p ** (r // 2),
                "budget": budget, "theta": theta or 0, "runs": runs}
    statistics = {"cells": budget * budget, "tiles": count * count, "band_cells": store.band_cells,
                  "path_tiles": len(entries), "peak_wave_tiles": peak_tiles}
    return document, statistics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("p", type=int)
    parser.add_argument("r", type=int)
    parser.add_argument("--tile-side", type=int, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    arguments = parser.parse_args()
    document, statistics = solve(arguments.p, arguments.r, arguments.tile_side, arguments.work_dir)
    print(json.dumps({"statistics": statistics, "theta": document["theta"], "runs": len(document["runs"])},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
