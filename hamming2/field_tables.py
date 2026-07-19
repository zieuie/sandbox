#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from odd import GaloisElement, GaloisField, parse_pr, sud_sets


def parse_prim(parts: Sequence[str]) -> list[int]:
  if len(parts) == 1 and "," in parts[0]:
    parts = [part for part in parts[0].split(",") if part]
  values = [int(part) for part in parts]
  if len(values) < 2:
    raise ValueError("primitive polynomial must have at least two coefficients")
  return values


def coeffs_to_polynomial(coeffs: Sequence[int], variable: str = "x") -> str:
  terms: list[str] = []
  for power, coeff in enumerate(coeffs):
    if coeff == 0:
      continue
    if power == 0:
      term = str(coeff)
    elif power == 1:
      term = variable if coeff == 1 else f"{coeff}{variable}"
    else:
      term = f"{variable}^{power}" if coeff == 1 else f"{coeff}{variable}^{power}"
    terms.append(term)
  return " + ".join(terms) if terms else "0"


def coeffs_to_tuple(coeffs: Sequence[int]) -> str:
  return "(" + ", ".join(map(str, coeffs)) + ")"


def suffix_coeffs(suffix: int, length: int, base: int) -> tuple[int, ...]:
  digits = []
  value = suffix
  for _ in range(length):
    value, digit = divmod(value, base)
    digits.append(digit)
  return tuple(digits)


def element_cell(element: GaloisElement) -> tuple[int, int]:
  return element.pre(), element.suf()


def mapping_table(field: GaloisField) -> str:
  lines = [
    "## Element Labels",
    "",
    "| index | coefficients | polynomial | sud residue | sud suffix |",
    "|---:|:---|:---|---:|---:|",
  ]
  for label in range(field.Q):
    coeffs = field.coeffs(label)
    residue, suffix = element_cell(field.elements[label])
    lines.append(
      f"| {label} | `{coeffs_to_tuple(coeffs)}` | `{coeffs_to_polynomial(coeffs)}` | {residue} | {suffix} |"
    )
  return "\n".join(lines)


def sud_table(field: GaloisField) -> str:
  sets = sud_sets(field)
  suffix_count = field.P**field.L
  lines = [
    "## Sudborough Sets",
    "",
    "| set | residue | suffix | suffix coefficients | element indices | elements as polynomials |",
    "|---:|---:|---:|:---|:---|:---|",
  ]
  for set_index, elements in enumerate(sets):
    residue, suffix = divmod(set_index, suffix_count)
    labels = [int(element) for element in elements]
    polynomials = [coeffs_to_polynomial(field.coeffs(label)) for label in labels]
    lines.append(
      "| "
      + " | ".join(
        [
          str(set_index),
          str(residue),
          str(suffix),
          f"`{coeffs_to_tuple(suffix_coeffs(suffix, field.L, field.P))}`",
          ", ".join(map(str, labels)) or "-",
          ", ".join(f"`{poly}`" for poly in polynomials) or "-",
        ]
      )
      + " |"
    )
  return "\n".join(lines)


def build_markdown(field: GaloisField) -> str:
  prim = coeffs_to_polynomial(field.prim)
  return "\n\n".join(
    [
      f"# GF({field.P}^{field.R}) Tables",
      f"Primitive polynomial: `{coeffs_to_tuple(field.prim)}` = `{prim}`",
      f"Field order: `{field.Q}`",
      mapping_table(field),
      sud_table(field),
      "",
    ]
  )


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Print markdown tables for field labels and Sudborough sets.")
  parser.add_argument("q", help="Prime power, preferably as P^R, such as 2^5")
  parser.add_argument(
    "primitive_polynomial",
    nargs="+",
    help="Primitive polynomial coefficients low-to-high, e.g. 1 0 0 1 0 1",
  )
  parser.add_argument("-o", "--output", help="Write markdown to this file instead of stdout")
  return parser


def main() -> int:
  parser = build_parser()
  args = parser.parse_args()
  try:
    prime, degree = parse_pr(args.q)
    prim = parse_prim(args.primitive_polynomial)
  except ValueError as exc:
    parser.error(str(exc))

  if len(prim) != degree + 1:
    parser.error(f"expected {degree + 1} primitive-polynomial coefficients for degree {degree}")

  field = GaloisField(prime, degree, prim)
  markdown = build_markdown(field)
  if args.output:
    Path(args.output).write_text(markdown)
  else:
    print(markdown, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
