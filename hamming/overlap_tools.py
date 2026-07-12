#!/usr/bin/env python3

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Sequence

from odd import GaloisField, dp_split, parse_pr, sud_sets


@dataclass(frozen=True)
class RequestNode:
    block: int
    residue: int
    suffix: int


@dataclass
class Instance:
    field: GaloisField
    prime: int
    degree: int
    suffix_count: int
    block_widths: list[int]
    sud_sets_data: list[list[object]]

    @property
    def block_count(self) -> int:
        return len(self.block_widths)


def parse_q(text: str) -> tuple[int, int]:
    return parse_pr(text)


def build_instance(q_text: str, prim: Sequence[int] | None = None) -> Instance:
    prime, degree = parse_q(q_text)
    field = GaloisField(prime, degree, prim)
    split_value, split = dp_split(prime, degree)
    _ = split_value
    suffix_count = prime ** (degree >> 1)
    block_widths: list[int] = []
    for width, _b, multiplicity in split:
        block_widths.extend([width] * multiplicity)
    return Instance(
        field=field,
        prime=prime,
        degree=degree,
        suffix_count=suffix_count,
        block_widths=block_widths,
        sud_sets_data=sud_sets(field),
    )


def interval_design(instance: Instance) -> list[list[int]]:
    return [list(range(width)) for width in instance.block_widths]


def spread_design(instance: Instance) -> list[list[int]]:
    design = []
    for block, width in enumerate(instance.block_widths):
        step = (block % (instance.prime - 1)) + 1
        residues = []
        seen = set()
        current = block % instance.prime
        while len(residues) < width:
            if current not in seen:
                residues.append(current)
                seen.add(current)
            current = (current + step) % instance.prime
        design.append(residues)
    return design


def primitive_root_mod_prime(prime: int) -> int:
    factors = prime_factorization(prime - 1)
    for candidate in range(2, prime):
        if all(pow(candidate, (prime - 1) // factor, prime) != 1 for factor in factors):
            return candidate
    raise ValueError(f"could not find primitive root mod {prime}")


def orbit_design(instance: Instance) -> list[list[int]]:
    root = primitive_root_mod_prime(instance.prime)
    nonzero_orbit = [1]
    while len(nonzero_orbit) < instance.prime - 1:
        nonzero_orbit.append((nonzero_orbit[-1] * root) % instance.prime)
    full_order = [0] + nonzero_orbit
    design = []
    for block, width in enumerate(instance.block_widths):
        rotated = full_order[block % instance.prime :] + full_order[: block % instance.prime]
        design.append(rotated[:width])
    return design


def prime_factorization(n: int) -> list[int]:
    factors = []
    d = 2
    value = n
    while d * d <= value:
        if value % d == 0:
            factors.append(d)
            while value % d == 0:
                value //= d
        d += 1
    if value > 1:
        factors.append(value)
    return factors


def consecutive_slopes(instance: Instance) -> list[int]:
    return list(range(1, instance.block_count + 1))


def subgroup_slopes(instance: Instance) -> list[int]:
    order = instance.field.Q - 1
    divisors = sorted(d for d in divisors_of(order) if d >= instance.block_count)
    if not divisors:
        raise ValueError("no subgroup large enough for requested block count")
    subgroup_size = divisors[0]
    step = order // subgroup_size
    return [1 + ((i * step) % order) for i in range(instance.block_count)]


def randomish_slopes(instance: Instance) -> list[int]:
    order = instance.field.Q - 1
    step = max(1, order // max(1, instance.block_count))
    return [1 + ((i * step + i * i) % order) for i in range(instance.block_count)]


def divisors_of(n: int) -> list[int]:
    ret = set()
    for d in range(1, int(n**0.5) + 1):
        if n % d == 0:
            ret.add(d)
            ret.add(n // d)
    return sorted(ret)


def build_request_nodes(instance: Instance, residue_design: Sequence[Sequence[int]]) -> list[RequestNode]:
    nodes = []
    for block, residues in enumerate(residue_design):
        for residue in residues:
            for suffix in range(instance.suffix_count):
                nodes.append(RequestNode(block, residue, suffix))
    return nodes


def candidate_set(instance: Instance, node: RequestNode, slope_label: int) -> frozenset[int]:
    ret = set()
    cell = instance.sud_sets_data[node.suffix + node.residue * instance.suffix_count]
    slope_inverse = instance.field.inv(slope_label)
    for element in cell:
        ret.add(instance.field.mul(slope_inverse, int(element)))
    return frozenset(ret)


def build_candidate_family(
    instance: Instance,
    residue_design: Sequence[Sequence[int]],
    slopes: Sequence[int],
) -> dict[RequestNode, frozenset[int]]:
    family = {}
    for node in build_request_nodes(instance, residue_design):
        family[node] = candidate_set(instance, node, slopes[node.block])
    return family


def slope_ratio(field: GaloisField, left: int, right: int) -> int:
    return field.mul(left, field.inv(right))


def conflict_graph_stats(
    instance: Instance,
    family: dict[RequestNode, frozenset[int]],
    slopes: Sequence[int],
) -> dict[str, object]:
    nodes = list(family)
    adjacency = {node: set() for node in nodes}
    intersection_hist = Counter()
    ratio_stats: dict[int, Counter] = defaultdict(Counter)
    pair_count = 0

    for i, left_node in enumerate(nodes):
        left_set = family[left_node]
        for right_node in nodes[:i]:
            pair_count += 1
            overlap = len(left_set & family[right_node])
            if overlap == 0:
                continue
            adjacency[left_node].add(right_node)
            adjacency[right_node].add(left_node)
            intersection_hist[overlap] += 1
            ratio = slope_ratio(instance.field, slopes[left_node.block], slopes[right_node.block])
            ratio_stats[ratio][overlap] += 1

    component_sizes = []
    seen = set()
    for node in nodes:
        if node in seen:
            continue
        size = 0
        queue = deque([node])
        seen.add(node)
        while queue:
            current = queue.popleft()
            size += 1
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        component_sizes.append(size)
    component_sizes.sort(reverse=True)

    degrees = [len(adjacency[node]) for node in nodes]
    degree_hist = Counter(degrees)
    ratio_summary = {
        ratio: {
            "pairs_with_overlap": sum(counter.values()),
            "intersection_hist": dict(sorted(counter.items())),
        }
        for ratio, counter in sorted(ratio_stats.items())
    }

    return {
        "node_count": len(nodes),
        "pair_count": pair_count,
        "edge_count": sum(len(v) for v in adjacency.values()) // 2,
        "component_sizes": component_sizes,
        "degree_hist": dict(sorted(degree_hist.items())),
        "intersection_hist": dict(sorted(intersection_hist.items())),
        "ratio_summary": ratio_summary,
    }


def summarize_stats(stats: dict[str, object]) -> str:
    component_sizes = stats["component_sizes"]
    largest = component_sizes[:10]
    return "\n".join(
        [
            f"nodes: {stats['node_count']}",
            f"pairs checked: {stats['pair_count']}",
            f"conflict edges: {stats['edge_count']}",
            f"largest components: {largest}",
            f"degree histogram: {stats['degree_hist']}",
            f"intersection histogram: {stats['intersection_hist']}",
        ]
    )
