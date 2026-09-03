#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

from odd import GaloisElement, GaloisField, gammainverse, parse_prim, sud_sets


def cell_index(residue: int, suffix: int, suffix_count: int) -> int:
  return residue * suffix_count + suffix


def parse_primitive(parts: Sequence[str] | None) -> list[int] | None:
  if not parts:
    return None
  if len(parts) == 1 and "," in parts[0]:
    parts = [part for part in parts[0].split(",") if part]
  return parse_prim(",".join(parts))


def load_pattern(path: Path) -> dict[str, object]:
  data = json.loads(path.read_text())
  if data.get("filetype") != "odd_peek_pattern":
    raise ValueError(f"{path} is not an odd_peek pattern file")
  return data


def build_q_sets(
  prime: int,
  degree: int,
  split: Sequence[Sequence[int]],
  sets: Sequence[Sequence[GaloisElement]],
) -> list[list[int]]:
  suffix_count = prime ** (degree // 2)
  q_sets: list[list[int]] = []
  j = 0
  for _a, b, t in split:
    grouped = [[] for _ in range(t)]
    for k in range(b * t):
      suffix, residue = divmod(j, prime)
      grouped[k % t].extend(int(x) for x in sets[cell_index(residue, suffix, suffix_count)])
      j += 1
    q_sets.extend(sorted(group) for group in grouped)
  return q_sets


def build_left_requests(prime: int, degree: int, split: Sequence[Sequence[int]]) -> list[tuple[int, int, int]]:
  suffix_count = prime ** (degree // 2)
  requests: list[tuple[int, int, int]] = []
  block = 0
  for a, _b, t in split:
    for _ in range(t):
      for residue in range(a):
        for suffix in range(suffix_count):
          requests.append((block, residue, suffix))
      block += 1
  return requests


def request_neighbors(
  request: tuple[int, int, int],
  field: GaloisField,
  sets: Sequence[Sequence[GaloisElement]],
) -> list[int]:
  block, residue, suffix = request
  suffix_count = field.P**field.L
  inverse = field.inv(block + 1)
  return sorted(field.mul(inverse, int(x)) for x in sets[cell_index(residue, suffix, suffix_count)])


def deterministic_matching(
  left: Sequence[tuple[int, int, int]],
  right_count: int,
  neighbor_lists: Sequence[Sequence[int]],
) -> list[int] | None:
  pair_u = [-1] * len(left)
  pair_v = [-1] * right_count
  dist = [0] * len(left)

  def bfs() -> bool:
    queue: list[int] = []
    for u in range(len(left)):
      if pair_u[u] == -1:
        dist[u] = 0
        queue.append(u)
      else:
        dist[u] = -1
    found = False
    head = 0
    while head < len(queue):
      u = queue[head]
      head += 1
      for v in neighbor_lists[u]:
        mate = pair_v[v]
        if mate == -1:
          found = True
        elif dist[mate] == -1:
          dist[mate] = dist[u] + 1
          queue.append(mate)
    return found

  def dfs(u: int) -> bool:
    for v in neighbor_lists[u]:
      mate = pair_v[v]
      if mate == -1 or (dist[mate] == dist[u] + 1 and dfs(mate)):
        pair_u[u] = v
        pair_v[v] = u
        return True
    dist[u] = -1
    return False

  while bfs():
    for u in range(len(left)):
      if pair_u[u] == -1:
        dfs(u)

  return pair_u if all(v != -1 for v in pair_u) else None


def p_sets_from_matching(left: Sequence[tuple[int, int, int]], pair_u: Sequence[int], block_count: int) -> list[list[int]]:
  p_sets = [[] for _ in range(block_count)]
  for request, position in zip(left, pair_u):
    p_sets[request[0]].append(position)
  return [sorted(block) for block in p_sets]


def covered_rows(field: GaloisField, block_index: int, positions: Sequence[int], symbols: Sequence[int]) -> int:
  symbol_set = set(symbols)
  multiplier = block_index + 1
  return sum(
    1
    for translate in range(field.Q)
    if any(field.add(field.mul(multiplier, position), translate) in symbol_set for position in positions)
  )


def row_count_checks(field: GaloisField, p_sets: Sequence[Sequence[int]], q_sets: Sequence[Sequence[int]]) -> dict[str, object]:
  active_rows_by_block = [
    covered_rows(field, block_index, positions, symbols)
    for block_index, (positions, symbols) in enumerate(zip(p_sets, q_sets))
  ]
  freebie_rows = field.Q if len(p_sets) + 1 < field.Q else 0
  return {
    "active_blocks": len(p_sets),
    "active_rows_by_block": active_rows_by_block,
    "active_rows": sum(active_rows_by_block),
    "freebie_rows": freebie_rows,
    "total_rows": sum(active_rows_by_block) + freebie_rows,
  }


def canonical_hash(payload: dict[str, object]) -> str:
  text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(text.encode()).hexdigest()


def certificate_for(
  pattern_path: Path,
  primitive: Sequence[int] | None,
  check_coverage: bool,
) -> dict[str, object]:
  pattern = load_pattern(pattern_path)
  prime = int(pattern["prime"])
  degree = int(pattern["degree"])
  split = [[int(x) for x in step] for step in pattern["split"]]  # type: ignore[index]
  field = GaloisField(prime, degree, primitive)
  sets = sud_sets(field)
  q_sets = build_q_sets(prime, degree, split, sets)
  left = build_left_requests(prime, degree, split)
  neighbor_lists = [request_neighbors(request, field, sets) for request in left]
  pair_u = deterministic_matching(left, field.Q, neighbor_lists)
  if pair_u is None:
    raise ValueError(
      "could not find a complete matching for this pattern/primitive polynomial pair"
    )
  p_sets = p_sets_from_matching(left, pair_u, len(q_sets))
  active_rows = str(pattern.get("active_rows", ""))
  bound = str(pattern.get("bound", ""))
  payload: dict[str, object] = {
    "filetype": "odd_pa_certificate",
    "version": 1,
    "source_pattern": str(pattern_path),
    "field": {
      "prime": prime,
      "degree": degree,
      "order": str(pattern.get("q", field.Q)),
      "primitive_polynomial": field.prim,
      "generator_label": field.generator,
      "labeling": "0, 1, alpha, alpha^2, ...",
    },
    "pattern": {
      "k": int(pattern["k"]),
      "axis": str(pattern["axis"]),
      "normalized_value": str(pattern["normalized_value"]),
      "active_rows": active_rows,
      "bound": bound,
      "split": split,
    },
    "matching": {
      "algorithm": "deterministic Hopcroft-Karp with sorted requests and sorted neighbors",
      "left_requests": len(left),
      "right_positions": field.Q,
      "matched_requests": len(pair_u),
    },
    "partition_and_extension": {
      "active_cosets": len(p_sets),
      "freebie_multiplier": len(p_sets) + 1 if len(p_sets) + 1 < field.Q else None,
      "new_symbol": field.Q,
      "P": p_sets,
      "Q": q_sets,
    },
  }
  if check_coverage:
    payload["row_count_check"] = row_count_checks(field, p_sets, q_sets)
  payload["certificate_sha256"] = canonical_hash(payload)
  return payload


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Read an odd_peek pattern, compute a deterministic matching, and write a compact PA certificate."
  )
  parser.add_argument("pattern", type=Path, help="odd_peek pattern JSON")
  parser.add_argument("-o", "--output", type=Path, help="Certificate JSON output path")
  parser.add_argument(
    "--prim",
    nargs="+",
    help="Primitive polynomial coefficients low-to-high. Accepts either '1,0,1,1' or '1 0 1 1'.",
  )
  parser.add_argument(
    "--check-coverage",
    action="store_true",
    help="Check that every active coset is covered before writing the certificate.",
  )
  return parser


def main() -> int:
  parser = build_parser()
  args = parser.parse_args()
  try:
    primitive = parse_primitive(args.prim)
    certificate = certificate_for(args.pattern, primitive, args.check_coverage)
  except ValueError as exc:
    parser.error(str(exc))

  output = args.output or args.pattern.with_suffix(".certificate.json")
  output.write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
  pe = certificate["partition_and_extension"]
  matching = certificate["matching"]
  print(f"Wrote certificate to {output}")
  print(
    f"Matched {matching['matched_requests']}/{matching['left_requests']} requests "
    f"across {pe['active_cosets']} active cosets."
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
