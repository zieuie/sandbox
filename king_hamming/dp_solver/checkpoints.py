"""Native DP checkpoint layout and validation; transport remains in cluster."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import sys
from typing import Any

DP_METADATA = struct.Struct("@8sIIIIIQQQQ")

def native_layout() -> dict[str, Any]:
    """Return this host's supported DP checkpoint layout."""

    return {"byteorder": sys.byteorder, "metadata_bytes": DP_METADATA.size,
            "value_bytes": 8, "choice_bytes": 4, "metadata_format": "KHDPCHK1"}

def dp_dimensions(specification: dict[str, Any]) -> dict[str, int]:
    """Return validated DP dimensions from specification; reject invalid powers or tiles."""

    arguments = specification.get("arguments", {})
    p = int(arguments["p"])
    r = int(arguments["r"])
    tile = int(arguments.get("tile_side", 4096))

    if not 2 <= p <= 1621 or not 3 <= r <= 31 or r % 2 == 0 or not 1 <= tile <= 0xFFFFFFFF:
        raise ValueError("invalid DP checkpoint parameters")

    if any(p % divisor == 0 for divisor in range(2, int(p**0.5) + 1)):
        raise ValueError("DP checkpoint modulus is not prime")

    q = p**r
    budget = p**((r + 1) // 2)
    if q > 2**64 - 1 or budget > 2**32 - 1 or (budget + 1)**2 * 12 > 2**64 - 1:
        raise ValueError("DP checkpoint dimensions exceed native integer width")
    return {"p": p, "r": r, "q": q, "budget": budget, "tile_side": tile}

def covered_cells(dimensions: dict[str, int], cursor: int) -> int:
    """Return committed cells for dimensions and cursor, rejecting out-of-range cursors."""

    budget = dimensions["budget"]
    tile = dimensions["tile_side"]
    rows = (budget + tile - 1) // tile

    if type(cursor) is not int or not 0 <= cursor <= rows * rows:
        raise ValueError("invalid DP checkpoint cursor")

    complete_rows = min(budget, (cursor // rows) * tile)
    height = min(tile, budget - complete_rows)
    return complete_rows * budget + height * (cursor % rows) * tile


def describe(manifest, specification, require_native=False):
    """Return expected member sizes and coverage after validating DP identity and layout."""
    cursor = manifest["cursor"]
    dimensions = dp_dimensions(specification)
    expected_done = covered_cells(dimensions, cursor)
    total = dimensions["budget"]**2
    cells = (dimensions["budget"] + 1)**2
    layout = manifest.get("layout", {})

    if manifest.get("parameters") != dimensions or layout.get("metadata_format") != "KHDPCHK1":
        raise ValueError("checkpoint dimensions or metadata format mismatch")

    if layout.get("byteorder") not in {"little", "big"} or layout.get("value_bytes") != 8 or layout.get("choice_bytes") != 4:
        raise ValueError("unsupported checkpoint integer layout")

    if type(layout.get("metadata_bytes")) is not int or layout["metadata_bytes"] not in {60, 64}:
        raise ValueError("unsupported checkpoint metadata size")

    if require_native and layout != native_layout():
        raise ValueError("checkpoint native layout is incompatible with this host")

    sizes = {"values.bin": cells * 8, "choices.bin": cells * 4,
             "checkpoint.bin": layout["metadata_bytes"]}
    return sizes, expected_done, total

def validate_metadata(
    manifest: dict[str, Any], paths: dict[str, Path], require_native: bool = True,
) -> None:
    """Validate files' restart metadata against manifest; require native DP layout."""

    if require_native and manifest["layout"] != native_layout():
        raise ValueError("checkpoint native layout is incompatible with this host")

    data = paths["checkpoint.bin"].read_bytes()

    layout = manifest["layout"]
    endian = "<" if layout["byteorder"] == "little" else ">"
    padding = "4x" if layout["metadata_bytes"] == 64 else ""
    metadata = struct.Struct(endian + "8sIIIII" + padding + "QQQQ")

    if len(data) != metadata.size:
        raise ValueError("checkpoint metadata length mismatch")

    magic, version, p, r, budget, tile, cursor, tiles, values, choices = metadata.unpack(data)
    dimensions = manifest["parameters"]
    tile_rows = (budget + tile - 1) // tile if tile else 0
    expected = (b"KHDPCHK1", 1, dimensions["p"], dimensions["r"], dimensions["budget"],
                dimensions["tile_side"], manifest["cursor"], tile_rows**2,
                (dimensions["budget"] + 1)**2 * 8, (dimensions["budget"] + 1)**2 * 4)

    if (magic, version, p, r, budget, tile, cursor, tiles, values, choices) != expected:
        raise ValueError("DP checkpoint metadata disagrees with manifest")
