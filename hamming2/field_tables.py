#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from odd import GaloisElement, GaloisField, parse_pr, sud_sets, theorem


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


def labels_to_markdown(labels: Sequence[int]) -> str:
  return ", ".join(map(str, labels)) or "-"


def labels_to_polynomials(field: GaloisField, labels: Sequence[int]) -> str:
  return ", ".join(f"`{coeffs_to_polynomial(field.coeffs(label))}`" for label in labels) or "-"


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
    "| set | residue | suffix | suffix coefficients | element indices |",
    "|---:|---:|---:|:---|:---|",
  ]
  for set_index, elements in enumerate(sets):
    residue, suffix = divmod(set_index, suffix_count)
    labels = [int(element) for element in elements]
    lines.append(
      "| "
      + " | ".join(
        [
          str(set_index),
          str(residue),
          str(suffix),
          f"`{coeffs_to_tuple(suffix_coeffs(suffix, field.L, field.P))}`",
          labels_to_markdown(labels),
        ]
      )
      + " |"
    )
  return "\n".join(lines)


def partition_extension_table(field: GaloisField, p_sets: Sequence[Sequence[int]], q_sets: Sequence[Sequence[int]]) -> str:
  lines = [
    "## Partition-and-Extension Choices",
    "",
    "| block | AGL multiplier | positions P | symbols Q |",
    "|---:|---:|:---|:---|",
  ]
  for block_index, (positions, symbols) in enumerate(zip(p_sets, q_sets), start=1):
    sorted_positions = sorted(positions)
    sorted_symbols = sorted(symbols)
    lines.append(
      "| "
      + " | ".join(
        [
          str(block_index - 1),
          str(block_index),
          labels_to_markdown(sorted_positions),
          labels_to_markdown(sorted_symbols),
        ]
      )
      + " |"
    )

  freebie = len(p_sets) + 1
  if freebie < field.Q:
    lines.extend(
      [
        "",
        f"Freebie coset multiplier: `{freebie}`. These rows append the new symbol `{field.Q}` without moving any old symbol.",
      ]
    )
  return "\n".join(lines)


def build_markdown(field: GaloisField, include_pe: bool) -> str:
  prim = coeffs_to_polynomial(field.prim)
  sections = [
    f"# GF({field.P}^{field.R}) Tables",
    f"Primitive polynomial: `{coeffs_to_tuple(field.prim)}` = `{prim}`",
    f"Field order: `{field.Q}`",
    mapping_table(field),
    sud_table(field),
  ]
  if include_pe:
    p_sets, q_sets, pe_field, active_rows = theorem(field.P, field.R, prim=field.prim)
    sections.append(
      "\n".join(
        [
          f"## Partition-and-Extension Summary",
          "",
          f"Active cosets: `{len(p_sets)}`",
          f"Active rows: `{active_rows}`",
          f"Total rows with freebie coset: `{active_rows + field.Q}`",
        ]
      )
    )
    sections.append(partition_extension_table(pe_field, p_sets, q_sets))
  sections.append("")
  return "\n\n".join(sections)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Print markdown tables for field labels, Sudborough sets, and PE choices.")
  parser.add_argument("q", help="Prime power, preferably as P^R, such as 2^5")
  parser.add_argument(
    "primitive_polynomial",
    nargs="+",
    help="Primitive polynomial coefficients low-to-high, e.g. 1 0 0 1 0 1",
  )
  parser.add_argument("-o", "--output", help="Write markdown to this file instead of stdout")
  parser.add_argument("--no-pe", action="store_true", help="Skip the partition-and-extension P/Q choice tables")
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
  markdown = build_markdown(field, include_pe=not args.no_pe)
  if args.output:
    Path(args.output).write_text(markdown)
  else:
    print(markdown, end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
