#!/usr/bin/env python3

"""
Build Sudborough-style Hamming permutation arrays and write them as plain rows.

This is a fresh, file-grid version of `hamming/odd.py`.  The historical
dependencies (`xutils.xfield`, `xutils.xflow`, `xtar`, etc.) are not present in
this workspace, so this file keeps local replacements for the required pieces:

- a lightweight finite-field descriptor that tracks elements in a polynomial
  basis over GF(p),
- a Hopcroft-Karp maximum bipartite matching routine,
- simple helpers for parsing prime powers and writing permutation arrays.

The output is a text file where each line is one permutation, with entries
separated by spaces.
"""

from __future__ import annotations

import argparse
import math
import random
from collections import deque
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Callable, Iterable, Sequence


HELPSTR = """
odd.py is dedicated to Dr. Sudborough's odd powers.

It uses a parity argument to create a good coverage of AGL, then writes the
expanded partition-and-extension permutation array as a grid of numbers.

Usage:

python odd.py Q [OPTIONS]

Where:
Q is either a prime power or a string of the form P^R, where P is prime.

Options:

-p, --peek     Only compute the bound. No matching is made.
-v, --verbose  Print explanatory output
-n, --naive    Use the simpler full-coverage split
-o, --output   Choose the output file
""".strip()


def radix_tuple(n: int, length: int, base: int) -> tuple[int, ...]:
  digits = [0] * length
  for i in range(length):
    n, digits[i] = divmod(n, base)
  return tuple(reversed(digits))


def get_residue(n: int, base: int = 2) -> int:
  residue = 0
  while n:
    n, digit = divmod(n, base)
    residue = (residue + digit) % base
  return residue


def gamma(value: int, multiplier: int, field: "GaloisField") -> int:
  return field.mul(multiplier + 1, value)


def gammainverse(value: int, multiplier: int, field: "GaloisField") -> int:
  return field.mul(field.inv(multiplier + 1), value)


def is_prime(n: int) -> bool:
  if n < 2:
    return False
  if n % 2 == 0:
    return n == 2
  d = 3
  while d * d <= n:
    if n % d == 0:
      return False
    d += 2
  return True


def prime_power_from_int(value: int) -> tuple[int, int]:
  if value < 2:
    raise ValueError(f"{value} is not a prime power")
  if is_prime(value):
    return value, 1
  for p in range(2, value + 1):
    if not is_prime(p):
      continue
    power = p
    exponent = 1
    while power < value:
      power *= p
      exponent += 1
    if power == value:
      return p, exponent
  raise ValueError(f"{value} is not a prime power")


def parse_pr(text: str) -> tuple[int, int]:
  if "^" in text:
    left, right = text.split("^", 1)
    p = int(left)
    r = int(right)
    if not is_prime(p):
      raise ValueError(f"{p} is not prime")
    if r < 1:
      raise ValueError("the exponent must be positive")
    return p, r
  return prime_power_from_int(int(text))


def digits_to_int(digits: Sequence[int], base: int) -> int:
  total = 0
  multiplier = 1
  for digit in digits:
    total += digit * multiplier
    multiplier *= base
  return total


def poly_strip(poly: Sequence[int]) -> tuple[int, ...]:
  data = list(poly)
  while data and data[-1] == 0:
    data.pop()
  return tuple(data)


def poly_mul_mod(a: Sequence[int], b: Sequence[int], modulus_poly: Sequence[int], p: int) -> tuple[int, ...]:
  mod = list(modulus_poly)
  degree = len(mod) - 1
  prod = [0] * (len(a) + len(b) - 1)
  for i, av in enumerate(a):
    for j, bv in enumerate(b):
      prod[i + j] = (prod[i + j] + av * bv) % p
  while len(prod) > degree:
    lead = prod[-1] % p
    if lead:
      offset = len(prod) - len(mod)
      for i, coeff in enumerate(mod):
        prod[offset + i] = (prod[offset + i] - lead * coeff) % p
    prod.pop()
  return tuple(x % p for x in prod)


def poly_mod(dividend: Sequence[int], divisor: Sequence[int], p: int) -> tuple[int, ...]:
  rem = list(dividend)
  div = list(poly_strip(divisor))
  if not div:
    raise ZeroDivisionError("polynomial division by zero")
  inv_lead = pow(div[-1], -1, p)
  while len(rem) >= len(div) and any(rem):
    coeff = rem[-1] * inv_lead % p
    offset = len(rem) - len(div)
    for i, value in enumerate(div):
      rem[offset + i] = (rem[offset + i] - coeff * value) % p
    while rem and rem[-1] == 0:
      rem.pop()
  return tuple(rem)


def is_irreducible(poly: Sequence[int], p: int) -> bool:
  degree = len(poly) - 1
  for d in range(1, degree // 2 + 1):
    for coeffs in product(range(p), repeat=d):
      candidate = coeffs + (1,)
      if poly_mod(poly, candidate, p) == ():
        return False
  return True


def proper_divisors(n: int) -> list[int]:
  divs = set()
  d = 2
  temp = n
  while d * d <= temp:
    if temp % d == 0:
      divs.add(d)
      while temp % d == 0:
        temp //= d
    d += 1
  if temp > 1:
    divs.add(temp)
  return sorted({n // d for d in divs})


def is_primitive(poly: Sequence[int], p: int) -> bool:
  if not is_irreducible(poly, p):
    return False
  degree = len(poly) - 1
  order = p**degree - 1
  x = (0, 1)
  for factor in proper_divisors(order):
    if poly_pow_mod(x, factor, poly, p) == (1,):
      return False
  return True


def poly_pow_mod(base_poly: Sequence[int], exponent: int, modulus_poly: Sequence[int], p: int) -> tuple[int, ...]:
  result = (1,)
  base = tuple(base_poly)
  power = exponent
  while power:
    if power & 1:
      result = poly_mul_mod(result, base, modulus_poly, p)
    base = poly_mul_mod(base, base, modulus_poly, p)
    power >>= 1
  return result


def candidate_polynomial_from_index(index: int, p: int, degree: int) -> tuple[int, ...]:
  constant = index % (p - 1) + 1
  rest = index // (p - 1)
  coeffs = [constant]
  for _ in range(degree - 1):
    rest, digit = divmod(rest, p)
    coeffs.append(digit)
  return tuple(coeffs + [1])


def find_primitive_polynomial(p: int, degree: int, randomize: bool = False) -> list[int]:
  if randomize:
    candidate_count = (p - 1) * p ** (degree - 1)
    start = random.randrange(candidate_count)
    if candidate_count == 1:
      step = 0
    else:
      step = random.randrange(1, candidate_count)
      while math.gcd(step, candidate_count) != 1:
        step = random.randrange(1, candidate_count)

    for attempt in range(candidate_count):
      index = (start + attempt * step) % candidate_count
      poly = candidate_polynomial_from_index(index, p, degree)
      if is_primitive(poly, p):
        return list(poly)
    raise ValueError(f"could not find a primitive polynomial for GF({p}^{degree})")

  for coeffs in product(range(p), repeat=degree):
    if coeffs[0] == 0:
      continue
    poly = coeffs + (1,)
    if is_primitive(poly, p):
      return list(poly)
  raise ValueError(f"could not find a primitive polynomial for GF({p}^{degree})")


@dataclass(frozen=True)
class GaloisElement:
  field: "GaloisField"
  label: int

  def digits(self) -> tuple[int, ...]:
    return self.field.coeffs(self.label)

  def pre(self) -> int:
    return sum(self.digits()) % self.field.P

  def suf(self) -> int:
    return digits_to_int(self.digits()[: self.field.L], self.field.P)

  def __int__(self) -> int:
    return self.label


class GaloisField:
  def __init__(self, prime: int, degree: int, prim: Sequence[int] | None = None) -> None:
    if not is_prime(prime):
      raise ValueError(f"{prime} is not prime")
    if degree < 1:
      raise ValueError("degree must be positive")
    self.P = prime
    self.R = degree
    self.L = degree >> 1
    self.Q = prime**degree
    self.prim = list(prim) if prim is not None else find_primitive_polynomial(prime, degree)
    self.generator = self._find_primitive_element()
    self.label_to_basis = self._build_label_to_basis()
    self.basis_to_label = {basis: label for label, basis in enumerate(self.label_to_basis)}
    self.elements = [GaloisElement(self, label) for label in range(self.Q)]

  def _basis_digits(self, value: int) -> tuple[int, ...]:
    digits = []
    n = value
    for _ in range(self.R):
      n, digit = divmod(n, self.P)
      digits.append(digit)
    return tuple(digits)

  def _basis_add(self, left: int, right: int) -> int:
    return digits_to_int(
      [(a + b) % self.P for a, b in zip(self._basis_digits(left), self._basis_digits(right))],
      self.P,
    )

  def _basis_mul(self, left: int, right: int) -> int:
    product = poly_mul_mod(self._basis_digits(left), self._basis_digits(right), self.prim, self.P)
    digits = list(product) + [0] * (self.R - len(product))
    return digits_to_int(digits[: self.R], self.P)

  def _basis_pow(self, base: int, exponent: int) -> int:
    result = 1
    power = base
    exp = exponent
    while exp:
      if exp & 1:
        result = self._basis_mul(result, power)
      power = self._basis_mul(power, power)
      exp >>= 1
    return result

  def _find_primitive_element(self) -> int:
    if self.Q == 2:
      return 1
    order = self.Q - 1
    checks = proper_divisors(order)
    for candidate in range(2, self.Q):
      if all(self._basis_pow(candidate, factor) != 1 for factor in checks):
        return candidate
    raise ValueError(f"could not find a primitive element for GF({self.P}^{self.R})")

  def _build_label_to_basis(self) -> list[int]:
    labels = [0, 1]
    current = 1
    for _ in range(1, self.Q - 1):
      current = self._basis_mul(current, self.generator)
      labels.append(current)
    return labels

  def coeffs(self, label: int) -> tuple[int, ...]:
    return self._basis_digits(self.label_to_basis[label])

  def add(self, left: int, right: int) -> int:
    basis_sum = self._basis_add(self.label_to_basis[left], self.label_to_basis[right])
    return self.basis_to_label[basis_sum]

  def mul(self, left: int, right: int) -> int:
    if left == 0 or right == 0:
      return 0
    basis_product = self._basis_mul(self.label_to_basis[left], self.label_to_basis[right])
    return self.basis_to_label[basis_product]

  def inv(self, value: int) -> int:
    if value == 0:
      raise ZeroDivisionError("0 has no multiplicative inverse in a field")
    exponent = value - 1
    inverse_exponent = (self.Q - 1 - exponent) % (self.Q - 1)
    return inverse_exponent + 1


def maximum_bipartite_matching(
  left_nodes: Iterable[object],
  right_nodes: Iterable[object],
  neighbors: Callable[[object], Iterable[object]],
) -> set[tuple[object, object]]:
  left = list(left_nodes)
  right = set(right_nodes)
  adj = {u: [v for v in neighbors(u) if v in right] for u in left}
  pair_u = {u: None for u in left}
  pair_v = {v: None for v in right}
  dist: dict[object | None, int] = {}

  def bfs() -> bool:
    queue: deque[object] = deque()
    for u in left:
      if pair_u[u] is None:
        dist[u] = 0
        queue.append(u)
      else:
        dist[u] = float("inf")
    dist[None] = float("inf")
    while queue:
      u = queue.popleft()
      if dist[u] >= dist[None]:
        continue
      for v in adj[u]:
        mate = pair_v[v]
        if dist.get(mate, float("inf")) == float("inf"):
          dist[mate] = dist[u] + 1
          if mate is not None:
            queue.append(mate)
    return dist[None] != float("inf")

  def dfs(u: object | None) -> bool:
    if u is None:
      return True
    for v in adj[u]:
      mate = pair_v[v]
      if dist.get(mate, float("inf")) == dist[u] + 1 and dfs(mate):
        pair_u[u] = v
        pair_v[v] = u
        return True
    dist[u] = float("inf")
    return False

  while bfs():
    for u in left:
      if pair_u[u] is None:
        dfs(u)

  return {(u, v) for u, v in pair_u.items() if v is not None}


def sud_sets(field: GaloisField) -> list[list[GaloisElement]]:
  nl = field.P**field.L
  sets = [[] for _ in range(field.P ** (field.R // 2 + 1))]
  for element in field.elements:
    sets[element.pre() * nl + element.suf()].append(element)
  return sets


def overlap(n: int, a: int, b: int, t: int) -> int:
  ret = set()
  for g_big in range(a):
    for g_small in range(b):
      ret.add((g_small * t - g_big) % n)
  return len(ret)


def dp_split(n: int, r: int) -> tuple[int, list[tuple[int, int, int]]]:
  l = r >> 1
  f = n**l
  nf = n * f
  ff = f * f
  values = [[0] * (nf + 1) for _ in range(nf + 1)]
  steps: list[list[tuple[int, int, int] | None]] = [[None] * (nf + 1) for _ in range(nf + 1)]

  for p in range(1, nf + 1):
    for q in range(1, nf + 1):
      for a in range(1, min(n, p) + 1):
        for b in range(1, min(n, q) + 1):
          limit = min(n, p // a, q // b)
          for t in range(1, limit + 1):
            candidate = t * ff * overlap(n, a, b, t) + values[p - t * a][q - t * b]
            if candidate > values[p][q]:
              values[p][q] = candidate
              steps[p][q] = (a, b, t)

  ret: list[tuple[int, int, int]] = []
  p = nf
  q = nf
  while p > 0 and q > 0:
    step = steps[p][q]
    if step is None:
      break
    ret.append(step)
    a, b, t = step
    p -= a * t
    q -= b * t
  return values[nf][nf], ret


def mishy_q(n: int, r: int, split: Sequence[tuple[int, int, int]], sets: Sequence[Sequence[GaloisElement]]) -> list[list[int]]:
  q_sets: list[list[int]] = []
  l = r >> 1
  f = n**l
  j = 0
  for _a, b, t in split:
    grouped = [[] for _ in range(t)]
    for k in range(b * t):
      q, residue = divmod(j, n)
      grouped[k % t].extend(int(x) for x in sets[residue * f + q])
      j += 1
    q_sets.extend(grouped)
  return q_sets


def mishy_a(n: int, r: int, split: Sequence[tuple[int, int, int]]) -> set[tuple[int, int, int]]:
  l = r >> 1
  f = n**l
  k = 0
  ret = set()
  for a, _b, t in split:
    for _ in range(t):
      for g in range(a):
        for h in range(f):
          ret.add((k, g, h))
      k += 1
  return ret


def edge_set(k: int, g: int, h: int, field: GaloisField, sets: Sequence[Sequence[GaloisElement]]) -> set[int]:
  ret = set()
  for x in sets[h + g * field.P**field.L]:
    ret.add(gammainverse(int(x), k, field))
  return ret


def theorem(n: int, r: int, prim: Sequence[int] | None = None, verbose: bool = False) -> tuple[list[set[int]], list[list[int]], GaloisField, int]:
  if verbose:
    print(f"Computing a partition for {n}^{r} = {n**r}")
    print("Creating a Galois field...")

  field = GaloisField(n, r, prim)

  if verbose:
    print(f"\t* Field made: {field.prim}")
    print("Creating Sudborough style sets...")

  sets = sud_sets(field)

  if verbose:
    print("\t* Sets created!")
    print("Computing optimal split...")

  value, split = dp_split(n, r)

  if verbose:
    print(f"\t* Split calculated: {split} {value}")
    print("Arranging Q sets...")

  num_p = sum(step[2] for step in split)
  p_sets = [set() for _ in range(num_p)]
  q_sets = mishy_q(n, r, split, sets)

  if verbose:
    print(f"\t* Q sets arranged! {[len(q) for q in q_sets]}")
    print("Arranging P sets...")

  left = mishy_a(n, r, split)
  edges = maximum_bipartite_matching(left, range(n**r), lambda u: edge_set(u[0], u[1], u[2], field, sets))
  for left_node, right_node in edges:
    p_sets[left_node[0]].add(right_node)

  return p_sets, q_sets, field, value


def naive_edge(k: int, i: int, field: GaloisField, sets: Sequence[Sequence[GaloisElement]]) -> set[int]:
  return {gammainverse(int(x), k, field) for x in sets[i]}


def naive_q(n: int, l: int, sets: Sequence[Sequence[GaloisElement]]) -> list[list[int]]:
  f = n**l
  q_sets = []
  for residue in range(f):
    merged: list[int] = []
    for digit in range(n):
      merged.extend(int(x) for x in sets[f * digit + residue])
    q_sets.append(merged)
  return q_sets


def naive_a(n: int, l: int) -> set[tuple[int, int]]:
  f = n**l
  return {(k, i) for k in range(f) for i in range(f)}


def naive_theorem(n: int, r: int, prim: Sequence[int] | None = None, verbose: bool = False) -> tuple[list[set[int]], list[list[int]], GaloisField, int]:
  if verbose:
    print("Creating a Galois field...")

  field = GaloisField(n, r, prim)
  l = r >> 1
  f = n**l

  if verbose:
    print(f"\t* Field made: {field.prim}")
    print("Creating Sudborough style sets...")

  sets = sud_sets(field)

  if verbose:
    print("\t* Sets created!")
    print("Arranging Q sets...")

  p_sets = [set() for _ in range(f)]
  q_sets = naive_q(n, l, sets)

  if verbose:
    print(f"\t* Q sets arranged! {[len(q) for q in q_sets]}")
    print("Arranging P sets...")

  left = naive_a(n, l)
  edges = maximum_bipartite_matching(left, range(field.Q), lambda u: naive_edge(u[0], u[1], field, sets))
  for left_node, right_node in edges:
    p_sets[left_node[0]].add(right_node)

  if verbose:
    print(f"\t* P sets arranged! {[len(p) for p in p_sets]}")
  return p_sets, q_sets, field, n**r * len(p_sets)


def agl_coset_rows(field: GaloisField, multiplier: int) -> list[list[int]]:
  rows = []
  for translate in range(field.Q):
    rows.append([field.add(field.mul(multiplier, x), translate) for x in range(field.Q)])
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
  position_parts: Sequence[Iterable[int]],
  symbol_parts: Sequence[Sequence[int]],
) -> list[list[int]]:
  if len(position_parts) != len(symbol_parts):
    raise ValueError("P and Q must have the same number of blocks")

  rows: list[list[int]] = []
  for block_index, (positions, symbols) in enumerate(zip(position_parts, symbol_parts), start=1):
    symbol_set = set(symbols)
    for row in agl_coset_rows(field, block_index):
      position = covered_position(row, positions, symbol_set)
      if position is not None:
        rows.append(extend_row(row, position, field.Q))

  freebie_multiplier = len(position_parts) + 1
  if freebie_multiplier < field.Q:
    for row in agl_coset_rows(field, freebie_multiplier):
      rows.append(list(row) + [field.Q])

  return rows


def write_pa(path: Path, rows: Sequence[Sequence[int]]) -> None:
  path.write_text("".join(" ".join(map(str, row)) + "\n" for row in rows))



def markdown_table(headers: Sequence[object], rows: Sequence[Sequence[object]]) -> str:
  def cell(value: object) -> str:
    return str(value).replace("|", r"\|").replace("\n", "<br>")

  lines = [
    "| " + " | ".join(cell(x) for x in headers) + " |",
    "| " + " | ".join("---" for _ in headers) + " |",
  ]
  lines.extend("| " + " | ".join(cell(x) for x in row) + " |" for row in rows)
  return "\n".join(lines)


def suffix_digits(index: int, length: int, base: int) -> tuple[int, ...]:
  digits = []
  value = index
  for _ in range(length):
    value, digit = divmod(value, base)
    digits.append(digit)
  return tuple(digits)


def suffix_label(index: int, p: int, m: int) -> str:
  if m == 1:
    return str(index)
  return "(" + ",".join(map(str, suffix_digits(index, m, p))) + ")"


def special_set_info(
  r_index: int,
  p: int,
  m: int,
  sets: Sequence[Sequence[GaloisElement]],
) -> tuple[int, int, str, list[int]]:
  f = p**m
  suffix_index, prefix = divmod(r_index, p)
  if suffix_index >= f:
    raise IndexError(f"special-set index R_{r_index} is outside the available range")
  name = f"{prefix}-Suffix({suffix_label(suffix_index, p, m)})"
  elements = sorted(int(x) for x in sets[prefix * f + suffix_index])
  return prefix, suffix_index, name, elements


def format_int_set(values: Iterable[int]) -> str:
  return "{" + ", ".join(map(str, sorted(values))) + "}"


def format_list(values: Iterable[object]) -> str:
  return "{" + ", ".join(map(str, values)) + "}"


def dp_block_records(
  p: int,
  r: int,
  split: Sequence[tuple[int, int, int]],
) -> list[dict[str, object]]:
  m = r >> 1
  ff = p ** (2 * m)
  records: list[dict[str, object]] = []
  coset_start = 0
  for block_index, (a, b, t) in enumerate(split, start=1):
    x_values = list(range(a))
    y_values = [(y * t) % p for y in range(b)]
    differences = sorted({(y - x) % p for x in x_values for y in y_values})
    omega = len(differences)
    records.append({
      "block": block_index,
      "a": a,
      "b": b,
      "t": t,
      "coset_start": coset_start,
      "coset_end": coset_start + t - 1,
      "stripe_units": a * t,
      "special_units": b * t,
      "x": x_values,
      "y": y_values,
      "differences": differences,
      "omega": omega,
      "normalized_gain": t * omega,
      "coverage": t * ff * omega,
    })
    coset_start += t
  return records


def q_assignment_records(
  p: int,
  r: int,
  split: Sequence[tuple[int, int, int]],
  sets: Sequence[Sequence[GaloisElement]],
) -> list[dict[str, object]]:
  m = r >> 1
  records: list[dict[str, object]] = []
  r_cursor = 0
  coset_start = 0
  for block_index, (a, b, t) in enumerate(split, start=1):
    for ell in range(t):
      r_indices = [r_cursor + ell + h * t for h in range(b)]
      infos = [special_set_info(index, p, m, sets) for index in r_indices]
      prefixes = [info[0] for info in infos]
      suffixes = [info[1] for info in infos]
      names = [info[2] for info in infos]
      elements = sorted(x for info in infos for x in info[3])
      records.append({
        "block": block_index,
        "coset": coset_start + ell,
        "a": a,
        "b": b,
        "t": t,
        "r_indices": r_indices,
        "prefixes": prefixes,
        "suffixes": suffixes,
        "names": names,
        "elements": elements,
      })
    r_cursor += b * t
    coset_start += t
  return records


def deterministic_dp_matching(
  p: int,
  r: int,
  split: Sequence[tuple[int, int, int]],
  field: GaloisField,
  sets: Sequence[Sequence[GaloisElement]],
) -> tuple[list[tuple[int, int, int]], dict[tuple[int, int, int], int]]:
  left = sorted(mishy_a(p, r, split))
  edges = maximum_bipartite_matching(
    left,
    range(field.Q),
    lambda u: sorted(edge_set(u[0], u[1], u[2], field, sets)),
  )
  return left, {u: v for u, v in edges}


def build_markdown_tables(
  p: int,
  r: int,
  prim: Sequence[int] | None = None,
) -> str:
  if r % 2 == 0 or r < 3:
    raise ValueError("Markdown DP tables are only defined here for odd exponents at least 3")

  m = r >> 1
  q = p**r
  f = p**m
  field = GaloisField(p, r, prim)
  sets = sud_sets(field)
  coverage, split = dp_split(p, r)
  blocks = dp_block_records(p, r, split)
  q_records = q_assignment_records(p, r, split, sets)
  left, match_by_left = deterministic_dp_matching(p, r, split, field, sets)
  matching_complete = len(match_by_left) == len(left)

  out: list[str] = []
  out.append(f"# DP construction tables for GF({p}^{r})")
  out.append("")
  out.append(f"- q = {q}")
  out.append(f"- m = {m}")
  out.append(f"- primitive polynomial coefficients (low-to-high) = {field.prim}")
  out.append(f"- DP split = {split}")
  out.append(f"- DP coverage before the freebie = {coverage}")
  out.append(f"- matching saturates A = {'yes' if matching_complete else 'NO'} ({len(match_by_left)}/{len(left)} left vertices matched)")
  out.append("")

  out.append("## 1. Prefix/suffix grid and special sets")
  out.append("")
  out.append("Each cell is one special set.  R-indices use the global order: suffix first, then prefix.")
  out.append("")
  if f <= 12:
    headers = ["prefix"] + [f"Suffix({suffix_label(h, p, m)})" for h in range(f)]
    rows: list[list[object]] = []
    for g in range(p):
      row: list[object] = [g]
      for h in range(f):
        r_index = h * p + g
        elements = sorted(int(x) for x in sets[g * f + h])
        row.append(f"R_{r_index} = {g}-Suffix({suffix_label(h, p, m)})<br>{format_int_set(elements)}")
      rows.append(row)
    out.append(markdown_table(headers, rows))
  else:
    rows = []
    for h in range(f):
      for g in range(p):
        r_index = h * p + g
        elements = sorted(int(x) for x in sets[g * f + h])
        rows.append([r_index, g, suffix_label(h, p, m), format_int_set(elements)])
    out.append(markdown_table(["R", "prefix", "suffix", "special-set contents"], rows))
  out.append("")

  out.append("## 2. DP output by (a,b,t)-block")
  out.append("")
  dp_rows: list[list[object]] = []
  total_stripes = 0
  total_special = 0
  total_normalized = 0
  total_coverage = 0
  for rec in blocks:
    start = int(rec["coset_start"])
    end = int(rec["coset_end"])
    cosets = f"C_{start}" if start == end else f"C_{start} ... C_{end}"
    dp_rows.append([
      rec["block"],
      f"({rec['a']},{rec['b']},{rec['t']})",
      cosets,
      rec["stripe_units"],
      rec["special_units"],
      format_list(rec["x"]),
      format_list(rec["y"]),
      format_list(rec["differences"]),
      rec["omega"],
      rec["normalized_gain"],
      rec["coverage"],
    ])
    total_stripes += int(rec["stripe_units"])
    total_special += int(rec["special_units"])
    total_normalized += int(rec["normalized_gain"])
    total_coverage += int(rec["coverage"])
  dp_rows.append([
    "TOTAL", "", "", total_stripes, total_special, "", "", "", "", total_normalized, total_coverage
  ])
  out.append(markdown_table(
    ["block", "(a,b,t)", "cosets", "at stripes", "bt special sets", "X", "canonical Y", "Y-X mod p", "omega", "t*omega", "covered permutations"],
    dp_rows,
  ))
  out.append("")

  out.append("## 3. Round-robin assignment of special sets to Q_i")
  out.append("")
  q_rows: list[list[object]] = []
  for rec in q_records:
    q_rows.append([
      rec["block"],
      f"C_{rec['coset']}",
      f"({rec['a']},{rec['b']},{rec['t']})",
      ", ".join(f"R_{x}" for x in rec["r_indices"]),
      format_list(rec["prefixes"]),
      format_list(suffix_label(int(h), p, m) for h in rec["suffixes"]),
      "<br>".join(rec["names"]),
      format_int_set(rec["elements"]),
    ])
  out.append(markdown_table(
    ["block", "coset", "(a,b,t)", "R-indices dealt to Q_i", "prefixes", "suffixes", "special sets in Q_i", "Q_i contents"],
    q_rows,
  ))
  out.append("")

  out.append("## 4. Bipartite matching, grouped into stripe requirements")
  out.append("")
  out.append("For each requested stripe g in a coset, the table lists the matched field element for every suffix beta.")
  out.append("")

  coset_to_a: dict[int, int] = {}
  coset_to_block: dict[int, int] = {}
  coset_start = 0
  for block_index, (a, _b, t) in enumerate(split, start=1):
    for ell in range(t):
      coset_to_a[coset_start + ell] = a
      coset_to_block[coset_start + ell] = block_index
    coset_start += t

  stripe_rows: list[list[object]] = []
  for coset in sorted(coset_to_a):
    for g in range(coset_to_a[coset]):
      assignments = []
      positions = []
      for h in range(f):
        vertex = (coset, g, h)
        matched = match_by_left.get(vertex)
        assignments.append(f"Suffix({suffix_label(h, p, m)}) -> {matched if matched is not None else 'UNMATCHED'}")
        if matched is not None:
          positions.append(matched)
      stripe_rows.append([
        coset_to_block[coset],
        f"C_{coset}",
        g,
        "<br>".join(assignments),
        format_int_set(positions),
      ])
  out.append(markdown_table(
    ["block", "coset", "stripe prefix g", "matched position by suffix", "positions selected for this stripe requirement"],
    stripe_rows,
  ))
  out.append("")

  out.append("## 5. Vertex-level matching (appendix/detail table)")
  out.append("")
  vertex_rows: list[list[object]] = []
  for vertex in left:
    coset, g, h = vertex
    neighborhood = sorted(edge_set(coset, g, h, field, sets))
    vertex_rows.append([
      f"C_{coset}",
      g,
      suffix_label(h, p, m),
      str(vertex),
      format_int_set(neighborhood),
      match_by_left.get(vertex, "UNMATCHED"),
    ])
  out.append(markdown_table(
    ["coset", "g", "suffix beta", "left vertex", "N(v)", "matched position"],
    vertex_rows,
  ))
  out.append("")

  out.append("## 6. Final P and Q summary")
  out.append("")
  p_sets: dict[int, set[int]] = {coset: set() for coset in coset_to_a}
  for vertex, matched in match_by_left.items():
    p_sets[vertex[0]].add(matched)
  q_by_coset = {int(rec["coset"]): rec for rec in q_records}
  block_by_index = {int(rec["block"]): rec for rec in blocks}
  summary_rows: list[list[object]] = []
  for coset in sorted(coset_to_a):
    block_index = coset_to_block[coset]
    block = block_by_index[block_index]
    qrec = q_by_coset[coset]
    per_coset_coverage = (p ** (2 * m)) * int(block["omega"])
    summary_rows.append([
      f"C_{coset}",
      block_index,
      f"({block['a']},{block['b']},{block['t']})",
      format_int_set(p_sets[coset]),
      "<br>".join(qrec["names"]),
      format_int_set(qrec["elements"]),
      per_coset_coverage,
    ])
  out.append(markdown_table(
    ["coset", "block", "(a,b,t)", "P_i", "special sets making Q_i", "Q_i", "covered permutations"],
    summary_rows,
  ))
  out.append("")

  budget = p ** (m + 1)
  s4_s, s4_r = divmod(budget, p + 1)
  section4_bound = (1 + 2 * s4_s) * q + (s4_r * p) ** 2
  dp_bound = coverage + q
  out.append("## 7. Payoff")
  out.append("")
  out.append(markdown_table(
    ["quantity", "value"],
    [
      ["available stripe units", budget],
      ["available special-set units", budget],
      ["stripe units used", total_stripes],
      ["special-set units used", total_special],
      ["DP-covered permutations", coverage],
      ["freebie coset", q],
      ["DP bound M(q+1,q)", dp_bound],
      ["Section 4 bound", section4_bound],
      ["improvement", dp_bound - section4_bound],
      ["matching saturates A", "yes" if matching_complete else "NO"],
    ],
  ))
  out.append("")

  return "\n".join(out)


def latex_int_set(values: Iterable[int]) -> str:
  """Compact LaTeX set notation, intentionally omitting spaces."""
  return r"\{" + ",".join(map(str, sorted(values))) + r"\}"


def latex_r_list(values: Iterable[int]) -> str:
  return ",".join(fr"R_{{{value}}}" for value in values)


def latex_table(
  headers: Sequence[str],
  rows: Sequence[Sequence[str]],
  caption: str,
  label: str,
  colspec: str | None = None,
  font_size: str = r"\scriptsize",
  tabcolsep: float = 3.0,
  resize: bool = False,
) -> str:
  """Return a compact table environment suitable for the paper."""
  if colspec is None:
    colspec = "|" + "|".join("c" for _ in headers) + "|"

  lines = [
    r"\begin{table}[!ht]",
    r"\centering",
    font_size,
    fr"\setlength{{\tabcolsep}}{{{tabcolsep}pt}}",
  ]
  if resize:
    lines.append(r"\resizebox{\linewidth}{!}{%")
  lines.extend([
    fr"\begin{{tabular}}{{{colspec}}}",
    r"\hline",
    " & ".join(headers) + r" \\ \hline",
  ])
  for row in rows:
    lines.append(" & ".join(str(x) for x in row) + r" \\ \hline")
  lines.append(r"\end{tabular}")
  if resize:
    lines.append("}")
  lines.extend([
    fr"\caption{{{caption}}}",
    fr"\label{{{label}}}",
    r"\end{table}",
  ])
  return "\n".join(lines)


def build_latex_tables(
  p: int,
  r: int,
  prim: Sequence[int] | None = None,
) -> str:
  """Build compact LaTeX tables for the running DP example.

  The output is intentionally paper-sized rather than exhaustive: it omits the
  vertex-level matching and the expanded contents of every Q_i.
  """
  if r % 2 == 0 or r < 3:
    raise ValueError("LaTeX DP tables are only defined here for odd exponents at least 3")

  m = r >> 1
  q = p**r
  f = p**m
  field = GaloisField(p, r, prim)
  sets = sud_sets(field)
  coverage, split = dp_split(p, r)
  blocks = dp_block_records(p, r, split)
  q_records = q_assignment_records(p, r, split, sets)
  left, match_by_left = deterministic_dp_matching(p, r, split, field, sets)
  matching_complete = len(match_by_left) == len(left)

  out: list[str] = []
  out.append(fr"% Compact Section 5 tables for $GF({p}^{r})$ (q={q})")
  out.append(fr"% Primitive polynomial coefficients, low-to-high: {field.prim}")
  out.append(fr"% DP split: {split}")
  out.append(fr"% Matching saturates A: {'yes' if matching_complete else 'NO'} ({len(match_by_left)}/{len(left)})")
  out.append("")

  # 1. Special-set grid.  For small suffix spaces this is a compact p-by-p^m
  # grid.  Cells contain only the field elements; names such as R_i and
  # i-Suffix(beta) are deliberately omitted.
  if f <= 5:
    headers = [r"$i\backslash\beta$"] + [fr"${suffix_label(h, p, m)}$" for h in range(f)]
    rows: list[list[str]] = []
    for g in range(p):
      row = [fr"${g}$"]
      for h in range(f):
        elements = sorted(int(x) for x in sets[g * f + h])
        row.append("$" + latex_int_set(elements) + "$")
      rows.append(row)
    out.append(latex_table(
      headers, rows,
      caption=fr"Special sets of $GF({p}^{r})$. Rows give prefix sums $i$ and columns give suffixes $\beta$.",
      label="table:dp-special-sets",
      colspec="|c|" + "c|" * f,
      font_size=r"\scriptsize",
      tabcolsep=2.0,
      resize=True,
    ))
  else:
    rows = []
    for h in range(f):
      for g in range(p):
        elements = sorted(int(x) for x in sets[g * f + h])
        rows.append([fr"${g}$", fr"${suffix_label(h, p, m)}$", "$" + latex_int_set(elements) + "$"])
    out.append(latex_table(
      [r"prefix $i$", r"suffix $\beta$", "special set"], rows,
      caption=fr"Special sets of $GF({p}^{r})$.",
      label="table:dp-special-sets",
      colspec="|c|c|l|",
      font_size=r"\scriptsize",
      tabcolsep=3.0,
    ))
  out.append("")

  # 2. DP output: only the columns that carry the story in the paper.
  dp_rows: list[list[str]] = []
  total_stripes = total_special = total_coverage = 0
  for rec in blocks:
    start = int(rec["coset_start"])
    end = int(rec["coset_end"])
    cosets = fr"$C_{{{start}}}$" if start == end else fr"$C_{{{start}}}\text{{--}}C_{{{end}}}$"
    dp_rows.append([
      str(rec["block"]),
      fr"$({rec['a']},{rec['b']},{rec['t']})$",
      cosets,
      str(rec["stripe_units"]),
      str(rec["special_units"]),
      str(rec["omega"]),
      str(rec["coverage"]),
    ])
    total_stripes += int(rec["stripe_units"])
    total_special += int(rec["special_units"])
    total_coverage += int(rec["coverage"])
  dp_rows.append([r"$\Sigma$", "", "", str(total_stripes), str(total_special), "", str(total_coverage)])
  out.append(latex_table(
    ["block", r"$(a,b,t)$", "cosets", r"$at$", r"$bt$", r"$\omega$", "covered"],
    dp_rows,
    caption=fr"Output of the dynamic program for $GF({p}^{r})$.",
    label="table:dp-output",
    colspec="|c|c|c|c|c|c|r|",
    font_size=r"\small",
    tabcolsep=4.0,
  ))
  out.append("")

  # 3. Round-robin Q assignment.  R-indices are enough to show the dealing
  # pattern because R has already been defined in the prose.
  q_rows: list[list[str]] = []
  for rec in q_records:
    q_rows.append([
      str(rec["block"]),
      fr"$Q_{{{rec['coset']}}}$",
      "$" + latex_r_list(rec["r_indices"]) + "$",
    ])
  out.append(latex_table(
    ["block", "symbol set", "special sets"],
    q_rows,
    caption="Round-robin assignment of special sets to the symbol sets.",
    label="table:dp-round-robin",
    colspec="|c|c|l|",
    font_size=r"\small",
    tabcolsep=5.0,
  ))
  out.append("")

  # 4. Matching, collapsed to one row per P_i.  Each line in the last cell is
  # one stripe: g : {matched positions}.
  coset_to_a: dict[int, int] = {}
  coset_start = 0
  for a, _b, t in split:
    for ell in range(t):
      coset_to_a[coset_start + ell] = a
    coset_start += t

  matching_rows: list[list[str]] = []
  for coset in sorted(coset_to_a):
    stripe_lines = []
    for g in range(coset_to_a[coset]):
      positions = []
      for h in range(f):
        matched = match_by_left.get((coset, g, h))
        if matched is not None:
          positions.append(matched)
      stripe_lines.append(fr"$g={g}:\ {latex_int_set(positions)}$")
    stripes_cell = r"\begin{tabular}{@{}l@{}}" + r" \\ ".join(stripe_lines) + r"\end{tabular}"
    matching_rows.append([fr"$P_{{{coset}}}$", stripes_cell])
  out.append(latex_table(
    ["position set", "stripes selected by the matching"],
    matching_rows,
    caption="Position stripes selected by the bipartite matching.",
    label="table:dp-matching",
    colspec="|c|l|",
    font_size=r"\scriptsize",
    tabcolsep=4.0,
  ))
  out.append("")

  # 5. Compact payoff.
  budget = p ** (m + 1)
  s4_s, s4_r = divmod(budget, p + 1)
  section4_bound = (1 + 2 * s4_s) * q + (s4_r * p) ** 2
  dp_bound = coverage + q
  payoff_rows = [
    ["Section 4", str(section4_bound)],
    ["DP construction", str(dp_bound)],
    ["improvement", str(dp_bound - section4_bound)],
  ]
  out.append(latex_table(
    ["method", fr"lower bound on $M({q + 1},{q})$"],
    payoff_rows,
    caption=fr"Comparison of the Section 4 and DP bounds for $q={q}$.",
    label="table:dp-payoff",
    colspec="|l|r|",
    font_size=r"\small",
    tabcolsep=6.0,
  ))

  return "\n\n".join(out)

def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=HELPSTR, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("q", help="A prime power or an explicit P^R expression")
  parser.add_argument("-p", "--peek", action="store_true", help="Only compute the bound")
  parser.add_argument("-v", "--verbose", action="store_true", help="Print explanatory output")
  parser.add_argument("-n", "--naive", action="store_true", help="Use the naive split")
  parser.add_argument("-o", "--output", help="Output path for the plain permutation-array grid")
  parser.add_argument("--markdown-tables", metavar="PATH", help="Write Markdown tables describing the DP construction and exit (use - for stdout)")
  parser.add_argument("--latex-tables", metavar="PATH", help="Write compact LaTeX tables for the DP construction and exit (use - for stdout)")
  parser.add_argument(
    "--prim",
    help="Optional primitive polynomial coefficients, comma-separated in low-to-high order (example: 1,1,0,1)",
  )
  return parser


def parse_prim(text: str | None) -> list[int] | None:
  if text is None:
    return None
  values = [int(part.strip()) for part in text.split(",") if part.strip()]
  if len(values) < 2:
    raise ValueError("primitive polynomial must have at least two coefficients")
  return values


def hamming_distance(u, v):
  ret = 0
  for a,b in zip(u,v):
    if a != b:
      ret += 1
  return ret


def verify(A, required_distance):
  for ux, u in enumerate(A):
    for vx in range(ux):
      d = hamming_distance(u, A[vx])
      if d < required_distance:
        return False
  return True


def main() -> int:
  parser = build_parser()
  args = parser.parse_args()

  try:
    n, r = parse_pr(args.q)
    prim = parse_prim(args.prim)
  except ValueError as exc:
    parser.error(str(exc))

  nr = n**r
  if r % 2 == 0 or r < 3:
    print(f"{nr} is not an odd power of a prime")
    return 1

  if args.latex_tables:
    if args.naive:
      parser.error("--latex-tables describes the DP construction and cannot be combined with --naive")
    report = build_latex_tables(n, r, prim=prim)
    if args.latex_tables == "-":
      print(report)
    else:
      table_path = Path(args.latex_tables)
      table_path.write_text(report + "\n")
      print(f"Wrote compact LaTeX construction tables to {table_path}.")
    return 0

  if args.markdown_tables:
    if args.naive:
      parser.error("--markdown-tables describes the DP construction and cannot be combined with --naive")
    report = build_markdown_tables(n, r, prim=prim)
    if args.markdown_tables == "-":
      print(report)
    else:
      table_path = Path(args.markdown_tables)
      table_path.write_text(report + "\n")
      print(f"Wrote Markdown construction tables to {table_path}.")
    return 0

  if args.peek:
    if args.naive:
      value = nr * (n ** (r // 2))
    else:
      value, split = dp_split(n, r)
      if args.verbose:
        print(split)
    print(f"M({nr + 1}, {nr}) >= {value + nr} (probably)")
    return 0

  if args.naive:
    p_sets, q_sets, field, coverage = naive_theorem(n, r, prim=prim, verbose=args.verbose)
  else:
    p_sets, q_sets, field, coverage = theorem(n, r, prim=prim, verbose=args.verbose)

  rows = partition_and_extend(field, p_sets, q_sets)

  if verify(rows, nr):
    print('Verified')
  else:
    print('Failed!')

  coverage += nr
  output = Path(args.output or f"M_{nr + 1}_{nr}_{coverage}.pa.txt")
  write_pa(output, rows)

  print(f"Used primitive polynomial: {field.prim}")
  print(f"Computed M({nr + 1}, {nr}) >= {coverage}.")
  print(f"Wrote {len(rows)} rows to {output}.")

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
