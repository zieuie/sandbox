#!/usr/bin/env python3

"""
Print the raw AGL(1, p^r) rows over the finite field GF(p^r).

Rows are printed as permutations of 0..q-1, one affine map per line:

    x -> a*x + b

where a ranges over the nonzero field elements and b ranges over all field
elements. No partition-and-extension step is applied.
"""

from __future__ import annotations

import argparse
import sys

from odd import GaloisField, is_prime, parse_pr, parse_prim
from xtar_to_pa import agl_coset_rows


def parse_field_args(p_text: str, r_text: str | None) -> tuple[int, int]:
    if r_text is None:
        return parse_pr(p_text)

    p = int(p_text)
    r = int(r_text)
    if not is_prime(p):
        raise ValueError(f"{p} is not prime")
    if r < 1:
        raise ValueError("the degree must be positive")
    return p, r


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("p", help="Prime p, or a prime-power expression like P^R")
    parser.add_argument("r", nargs="?", help="Degree r when p is given separately")
    parser.add_argument(
        "--prim",
        help="Optional primitive polynomial coefficients, comma-separated in low-to-high order",
    )
    parser.add_argument("--summary", action="store_true", help="Print q and row count to stderr first")
    # parser.add_argument("-h", "--human", action="store_true", help="Print human friendly")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        p, r = parse_field_args(args.p, args.r)
        prim = parse_prim(args.prim)
    except ValueError as exc:
        parser.error(str(exc))

    q = p**r
    field = GaloisField(p, r, prim)

    # if args.summary:
    print(
        f"# AGL(1, {field.Q}) over GF({field.P}^{field.R}); rows={field.Q * (field.Q - 1)}",
        file=sys.stderr,
    )
    print(f"# primitive polynomial: {field.prim}", file=sys.stderr)

    for a in range(1, q):
        print()
        print(f'a = {a}')
        for b in range(0, q):
            for x in range(0, q):
                print(field.add(field.mul(a, x), b), end=' ')
            print()

        break



    return 0


if __name__ == "__main__":
    raise SystemExit(main())
