#!/usr/bin/env python3

"""
Build a deliberately simple Sudborough-block Partition-and-Extension instance.

This script skips the dynamic-programming split in odd.py.  It considers two
simple families of active affine cosets and greedily keeps blocks until their
symbol sets exhaust the field:

1. suffix-union symbols:
   Q is the union of every Sudborough set with one fixed suffix, while P has
   one representative for each suffix and all representatives land in residue 0
   after multiplication by the coset slope.

2. single-cell symbols:
   Q is one whole Sudborough set with residue 0 and one fixed suffix, while P
   has one representative from every Sudborough set after multiplication by the
   coset slope.

The kept blocks use a matching only to choose disjoint position representatives;
there is still no dynamic-programming optimization.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from odd import (
    GaloisElement,
    GaloisField,
    gammainverse,
    maximum_bipartite_matching,
    parse_pr,
    serialize_field,
    sud_sets,
)
from xtar_to_pa import partition_and_extend


def cell_index(residue: int, suffix: int, suffix_count: int) -> int:
    return residue * suffix_count + suffix


def cell_values(
    sets: Sequence[Sequence[GaloisElement]],
    residue: int,
    suffix: int,
    suffix_count: int,
) -> list[int]:
    return [int(element) for element in sets[cell_index(residue, suffix, suffix_count)]]


def representative_preimage(
    sets: Sequence[Sequence[GaloisElement]],
    residue: int,
    suffix: int,
    suffix_count: int,
    coset_index: int,
    field: GaloisField,
) -> int:
    cell = sets[cell_index(residue, suffix, suffix_count)]
    if not cell:
        raise ValueError(f"empty Sudborough cell residue={residue} suffix={suffix}")
    return gammainverse(int(cell[0]), coset_index, field)


def suffix_union_symbols(
    sets: Sequence[Sequence[GaloisElement]],
    suffix: int,
    field: GaloisField,
) -> list[int]:
    symbols: list[int] = []
    for residue in range(field.P):
        symbols.extend(cell_values(sets, residue, suffix, field.P**field.L))
    return symbols


def suffix_representative_positions(
    sets: Sequence[Sequence[GaloisElement]],
    coset_index: int,
    field: GaloisField,
) -> list[int]:
    suffix_count = field.P**field.L
    return sorted(
        representative_preimage(sets, 0, suffix, suffix_count, coset_index, field)
        for suffix in range(suffix_count)
    )


def all_cell_representative_positions(
    sets: Sequence[Sequence[GaloisElement]],
    coset_index: int,
    field: GaloisField,
) -> list[int]:
    suffix_count = field.P**field.L
    positions = []
    for residue in range(field.P):
        for suffix in range(suffix_count):
            positions.append(representative_preimage(sets, residue, suffix, suffix_count, coset_index, field))
    return sorted(positions)


def preimage_cell(
    sets: Sequence[Sequence[GaloisElement]],
    block_index: int,
    residue: int,
    suffix: int,
    field: GaloisField,
) -> set[int]:
    suffix_count = field.P**field.L
    return {
        gammainverse(int(element), block_index, field)
        for element in sets[cell_index(residue, suffix, suffix_count)]
    }


def realize_positions(
    field: GaloisField,
    sets: Sequence[Sequence[GaloisElement]],
    requests: Sequence[Sequence[tuple[int, int]]],
) -> list[list[int]]:
    left = {
        (block_index, request_index, residue, suffix)
        for block_index, block_requests in enumerate(requests)
        for request_index, (residue, suffix) in enumerate(block_requests)
    }
    edges = maximum_bipartite_matching(
        left,
        range(field.Q),
        lambda node: preimage_cell(sets, node[0], node[2], node[3], field),
    )
    if len(edges) != len(left):
        raise ValueError(f"could only realize {len(edges)} of {len(left)} requested positions")

    p_sets = [[] for _ in requests]
    for left_node, right_node in edges:
        p_sets[left_node[0]].append(right_node)
    return [sorted(block) for block in p_sets]


def dumb_sud(
    prime: int,
    degree: int,
    prim: Sequence[int] | None = None,
) -> tuple[list[list[int]], list[list[int]], GaloisField]:
    field = GaloisField(prime, degree, prim)
    sets = sud_sets(field)
    suffix_count = prime**field.L

    requests: list[list[tuple[int, int]]] = []
    q_sets: list[list[int]] = []
    used_symbols: set[int] = set()

    def add_block_if_symbols_unused(symbols: list[int], block_requests: list[tuple[int, int]]) -> bool:
        symbol_set = set(symbols)
        if used_symbols & symbol_set:
            return False
        used_symbols.update(symbol_set)
        q_sets.append(symbols)
        requests.append(block_requests)
        return True

    for suffix in range(suffix_count):
        add_block_if_symbols_unused(
            suffix_union_symbols(sets, suffix, field),
            [(0, position_suffix) for position_suffix in range(suffix_count)],
        )

    for suffix in range(suffix_count):
        add_block_if_symbols_unused(
            cell_values(sets, 0, suffix, suffix_count),
            [(residue, position_suffix) for residue in range(prime) for position_suffix in range(suffix_count)],
        )

    p_sets = realize_positions(field, sets, requests)
    return p_sets, q_sets, field


def block_is_fully_covered(field: GaloisField, block_index: int, positions: Sequence[int], symbols: Sequence[int]) -> bool:
    symbol_set = set(symbols)
    slope = block_index + 1
    for translate in range(field.Q):
        if not any(field.add(field.mul(slope, position), translate) in symbol_set for position in positions):
            return False
    return True


def fully_covered_blocks(
    field: GaloisField,
    p_sets: Sequence[Sequence[int]],
    q_sets: Sequence[Sequence[int]],
) -> int:
    return sum(
        1
        for block_index, (positions, symbols) in enumerate(zip(p_sets, q_sets))
        if block_is_fully_covered(field, block_index, positions, symbols)
    )


def write_xtar(path: Path, field: GaloisField, p_sets: Sequence[Sequence[int]], q_sets: Sequence[Sequence[int]]) -> None:
    path.write_text(
        json.dumps(
            {
                "filetype": "pe",
                "pa": {"type": "agl", "field": serialize_field(field)},
                "P": p_sets,
                "Q": q_sets,
            },
            indent=2,
            sort_keys=True,
        )
    )


def write_pa(path: Path, rows: Sequence[Sequence[int]]) -> None:
    path.write_text("".join(" ".join(map(str, row)) + "\n" for row in rows))


def parse_prim(text: str | None) -> list[int] | None:
    if text is None:
        return None
    values = [int(part.strip()) for part in text.split(",") if part.strip()]
    if len(values) < 2:
        raise ValueError("primitive polynomial must have at least two coefficients")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("q", help="A prime power or an explicit P^R expression")
    parser.add_argument("-o", "--output", help="Output path")
    parser.add_argument("--pa", action="store_true", help="Write the fully expanded permutation array instead of xtar JSON")
    parser.add_argument("--prim", help="Optional primitive polynomial coefficients, comma-separated low-to-high")
    parser.add_argument("--peek", action="store_true", help="Only print the candidate row count; do not write a file")
    parser.add_argument("--check-coverage", action="store_true", help="Verify that every active coset is fully covered")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        prime, degree = parse_pr(args.q)
        prim = parse_prim(args.prim)
    except ValueError as exc:
        parser.error(str(exc))

    if degree % 2 == 0 or degree < 3:
        print(f"{prime**degree} is not an odd power of a prime")
        return 1

    suffix_count = prime ** (degree // 2)
    active_cosets = suffix_count
    generated_rows = prime**degree * (active_cosets + 1)

    if args.peek:
        print(f"Candidate rows after adding the freebie coset: {generated_rows}")
        print(f"Active cosets: {active_cosets}; suffix count: {suffix_count}")
        return 0

    p_sets, q_sets, field = dumb_sud(prime, degree, prim)
    generated_rows = field.Q * (len(p_sets) + 1)
    if args.pa:
        output = Path(args.output or f"dumb_{field.Q + 1}_{field.Q}_{generated_rows}.pa.txt")
        rows = partition_and_extend(field, p_sets, q_sets)
        write_pa(output, rows)
    else:
        output = Path(args.output or f"dumb_{field.Q + 1}_{field.Q}_{generated_rows}.xtar.json")
        write_xtar(output, field, p_sets, q_sets)

    print(f"Used primitive polynomial: {field.prim}")
    print(f"Built {len(p_sets)} active cosets plus one freebie, for {generated_rows} candidate rows.")
    if args.check_coverage:
        covered = fully_covered_blocks(field, p_sets, q_sets)
        print(f"Fully covered active cosets: {covered}/{len(p_sets)}")
    print(f"Wrote {'expanded permutation array' if args.pa else 'xtar JSON'} to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
