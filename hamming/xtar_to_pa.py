#!/usr/bin/env python3

"""
Convert a JSON-backed xtar file into an explicit permutation array.

This script is meant for the reconstructed `odd.py` output in this repo. It:

1. reads a `filetype: "pe"` or `filetype: "pi"` payload,
2. rebuilds GF(q) from the stored primitive polynomial,
3. enumerates AGL(1, q),
4. applies a blockwise Partition-and-Extension construction, and
5. prints one resulting permutation per row on the symbols 0..q.

Conventions used here
---------------------
- The chosen AGL cosets are taken in numeric multiplier order:
  `a = 1, 2, ..., k`, where `k = len(P)`.
- The "freebie" coset is the next one, `a = k + 1`, when it exists.
- Block `i` uses the position set `P[i]` and symbol set `Q[i]`.
- If a permutation is covered in multiple allowed positions, the smallest
  position is used.

These conventions match the surviving shape of the old code and Sudborough's
slides closely enough to make the construction executable again, but the
original xtar format did not preserve every historical choice explicitly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Sequence

from odd import GaloisField


def load_xtar(path: Path, pi_index: int) -> tuple[dict[str, object], list[list[int]], list[list[int]]]:
    data = json.loads(path.read_text())
    filetype = data.get("filetype")
    if filetype == "pe":
        return data, data["P"], data["Q"]
    if filetype == "pi":
        p_sets = data["P1"][pi_index]
        q_sets = data["Q1"][pi_index]
        return data, p_sets, q_sets
    raise ValueError(f"unsupported filetype: {filetype!r}")


def agl_coset_rows(field: GaloisField, multiplier: int) -> list[list[int]]:
    rows = []
    for translate in range(field.Q):
        row = [field.add(field.mul(multiplier, x), translate) for x in range(field.Q)]
        rows.append(row)
    return rows


def covered_position(row: Sequence[int], positions: Iterable[int], symbols: set[int]) -> int | None:
    for position in sorted(positions):
        if row[position] in symbols:
            return position
    return None


def extend_row(row: Sequence[int], position: int, new_symbol: int) -> list[int]:
    displaced = row[position]
    extended = list(row)
    extended[position] = new_symbol
    extended.append(displaced)
    return extended


def partition_and_extend(
    field: GaloisField,
    position_parts: Sequence[Sequence[int]],
    symbol_parts: Sequence[Sequence[int]],
) -> list[list[int]]:
    if len(position_parts) != len(symbol_parts):
        raise ValueError("P and Q must have the same number of blocks")

    q = field.Q
    rows: list[list[int]] = []

    for block_index, (positions, symbols) in enumerate(zip(position_parts, symbol_parts), start=1):
        symbol_set = set(symbols)
        for row in agl_coset_rows(field, block_index):
            position = covered_position(row, positions, symbol_set)
            if position is not None:
                rows.append(extend_row(row, position, q))

    freebie_multiplier = len(position_parts) + 1
    if freebie_multiplier < q:
        for row in agl_coset_rows(field, freebie_multiplier):
            rows.append(list(row) + [q])

    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xtar", help="Path to a JSON-backed xtar file")
    parser.add_argument(
        "--pi-index",
        type=int,
        default=0,
        help="When filetype=pi, choose which replicated partition pair to use (default: 0)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a short summary to stderr before the rows",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.xtar)
    data, position_parts, symbol_parts = load_xtar(path, args.pi_index)

    field_info = data["pa"]["field"]
    field = GaloisField(
        field_info["prime"],
        field_info["degree"],
        field_info["primitive_polynomial"],
    )
    rows = partition_and_extend(field, position_parts, symbol_parts)

    if args.summary:
        import sys

        print(
            f"# q={field.Q} blocks={len(position_parts)} rows={len(rows)} filetype={data['filetype']}",
            file=sys.stderr,
        )

    for row in rows:
        print(" ".join(map(str, row)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
