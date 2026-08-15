"""Symmetry-reduced weighted Hoffman optimization.

The SDP is solved by eigenvector cutting planes and SciPy/HiGHS linear
programming. Each cut is a necessary Rayleigh-quotient inequality; separation
continues until every irreducible block is PSD to the requested tolerance.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import factorial, floor
from typing import Callable, Iterable, Sequence

import numpy as np
from scipy.optimize import linprog

from spectral.first.chebyshev_spectra import displacement, generate_ball, inverse
from spectral.second.phase_d import ball_block
from spectral.second.young_irreps import Partition, irrep_dimension, irrep_matrix, partitions_of_n

Permutation = tuple[int, ...]
ClassKey = tuple[object, ...]


def cycle_type(permutation: Sequence[int]) -> tuple[int, ...]:
    seen = [False] * len(permutation)
    lengths = []
    for start in range(len(permutation)):
        if seen[start]:
            continue
        current = start
        length = 0
        while not seen[current]:
            seen[current] = True
            length += 1
            current = permutation[current]
        lengths.append(length)
    return tuple(sorted(lengths, reverse=True))


def forbidden_permutations(n: int, d: int) -> list[Permutation]:
    return [p for p in generate_ball(n, d - 1) if displacement(p) > 0]


def weight_classes(n: int, d: int, family: str) -> list[tuple[ClassKey, list[Permutation]]]:
    classes: dict[ClassKey, list[Permutation]] = defaultdict(list)
    for permutation in forbidden_permutations(n, d):
        shell = displacement(permutation)
        if family == "shell":
            key: ClassKey = (shell,)
        elif family == "shell_cycle_type":
            key = (shell, cycle_type(permutation))
        else:
            raise ValueError(f"unknown weight family: {family}")
        classes[key].append(permutation)
    result = sorted(classes.items(), key=lambda item: repr(item[0]))
    for _, members in result:
        member_set = set(members)
        if any(inverse(p) not in member_set for p in members):
            raise AssertionError("weight class is not inverse-closed")
    return result


@dataclass
class WeightedProblem:
    n: int
    d: int
    family: str
    keys: list[ClassKey]
    sizes: np.ndarray
    partitions: list[Partition]
    blocks: dict[Partition, list[np.ndarray]]


def build_problem(n: int, d: int, family: str) -> WeightedProblem:
    classes = weight_classes(n, d, family)
    keys = [key for key, _ in classes]
    sizes = np.asarray([len(members) for _, members in classes], dtype=float)
    partitions = [shape for shape in partitions_of_n(n) if shape != (n,)]
    blocks: dict[Partition, list[np.ndarray]] = {}
    if family == "shell":
        balls = [list(generate_ball(n, radius)) for radius in range(d)]
        for shape in partitions:
            cumulative = [ball_block(shape, ball) for ball in balls]
            zero = np.zeros((irrep_dimension(shape),) * 2)
            blocks[shape] = [cumulative[j] - (cumulative[j - 1] if j else zero) for j in range(1, d)]
    else:
        for shape in partitions:
            class_blocks = []
            for _, members in classes:
                matrix = np.zeros((irrep_dimension(shape),) * 2)
                for permutation in members:
                    matrix += irrep_matrix(shape, permutation)
                class_blocks.append((matrix + matrix.T) / 2.0)
            blocks[shape] = class_blocks
    return WeightedProblem(n, d, family, keys, sizes, partitions, blocks)


def weighted_block(problem: WeightedProblem, partition: Partition, weights: np.ndarray) -> np.ndarray:
    result = np.zeros_like(problem.blocks[partition][0])
    for weight, class_block in zip(weights, problem.blocks[partition]):
        result += weight * class_block
    return (result + result.T) / 2.0


def _cut(problem: WeightedProblem, partition: Partition, vector: np.ndarray) -> np.ndarray:
    return np.asarray([float(vector @ matrix @ vector) for matrix in problem.blocks[partition]])


def _initial_cuts(problem: WeightedProblem) -> list[np.ndarray]:
    cuts: list[np.ndarray] = []
    rng = np.random.default_rng(20260815)
    for shape in problem.partitions:
        dimension = irrep_dimension(shape)
        vectors = list(np.eye(dimension))
        for matrix in problem.blocks[shape]:
            _, eigenvectors = np.linalg.eigh(matrix)
            vectors.extend(eigenvectors.T)
        for _ in range(min(4, dimension)):
            vector = rng.normal(size=dimension)
            vectors.append(vector / np.linalg.norm(vector))
        cuts.extend(_cut(problem, shape, vector) for vector in vectors)
    return cuts


def solve_weighted_problem(
    problem: WeightedProblem,
    tolerance: float = 1e-8,
    max_iterations: int = 500,
    weight_limit: float = 10_000.0,
) -> dict[str, object]:
    cuts = _initial_cuts(problem)
    weights: np.ndarray | None = None
    iterations = 0
    # HiGHS feasibility is itself floating point; below roughly 1e-7 it can
    # return the same boundary solution and cut indefinitely. The final uniform
    # rescaling below enforces the user-requested spectral tolerance.
    separation_tolerance = max(tolerance, 1e-7)
    for iterations in range(1, max_iterations + 1):
        result = linprog(
            -problem.sizes,
            A_ub=-np.asarray(cuts),
            b_ub=np.ones(len(cuts)),
            bounds=[(-weight_limit, weight_limit)] * len(problem.keys),
            method="highs",
        )
        if not result.success:
            raise RuntimeError(f"cutting-plane LP failed: {result.message}")
        weights = np.asarray(result.x)
        if np.any(np.abs(weights) >= weight_limit * 0.999):
            raise RuntimeError("artificial weight bound became active; increase weight_limit")
        violations = []
        for shape in problem.partitions:
            eigenvalues, eigenvectors = np.linalg.eigh(weighted_block(problem, shape, weights))
            if eigenvalues[0] < -1.0 - separation_tolerance:
                violations.append((float(eigenvalues[0]), _cut(problem, shape, eigenvectors[:, 0])))
        if not violations:
            break
        cuts.extend(cut for _, cut in violations)
    else:
        raise RuntimeError("cutting-plane solver did not converge")
    assert weights is not None

    minima = {shape: float(np.linalg.eigvalsh(weighted_block(problem, shape, weights))[0]) for shape in problem.partitions}
    minimum = min(minima.values())
    scale = 1.0
    if minimum < -1.0:
        scale = 1.0 / (-minimum)
        weights = weights * scale
        minima = {shape: value * scale for shape, value in minima.items()}
        minimum = min(minima.values())
    row_sum = float(problem.sizes @ weights)
    real_bound = factorial(problem.n) / (1.0 + row_sum)
    active = [list(shape) for shape, value in minima.items() if abs(value + 1.0) <= 2e-6]
    return {
        "n": problem.n,
        "d": problem.d,
        "weight_family": problem.family,
        "number_of_variables": len(problem.keys),
        "weight_classes": [list(key) for key in problem.keys],
        "class_sizes": problem.sizes.astype(int).tolist(),
        "weights": weights.tolist(),
        "row_sum": row_sum,
        "spectral_upper_bound_real": real_bound,
        "spectral_upper_bound_floor": floor(real_bound + 1e-7),
        "active_partitions": active,
        "minimum_block_eigenvalue": minimum,
        "block_minima": {str(list(shape)): value for shape, value in minima.items()},
        "solver": "SciPy HiGHS eigenvector cutting planes",
        "solver_tolerance": tolerance,
        "iterations": iterations,
        "feasibility_rescale": scale,
    }


def ordinary_hoffman(problem: WeightedProblem) -> dict[str, float | int | list[list[int]]]:
    weights = np.ones(len(problem.keys))
    minima = {shape: float(np.linalg.eigvalsh(weighted_block(problem, shape, weights))[0]) for shape in problem.partitions}
    tau = min(minima.values())
    row_sum = float(problem.sizes.sum())
    bound = factorial(problem.n) * (-tau) / (row_sum - tau)
    active = [list(shape) for shape, value in minima.items() if abs(value - tau) <= 1e-8]
    return {"row_sum": row_sum, "least_eigenvalue": tau, "real_bound": bound,
            "integer_bound": floor(bound + 1e-7), "active_partitions": active}
