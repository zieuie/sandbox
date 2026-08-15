"""Phase A--C computations for Chebyshev-distance graphs on S_n.

Permutations are zero-based tuples.  Composition is the function convention
``compose(p, q)[i] == p[q[i]]``.
"""

from __future__ import annotations

from itertools import permutations
from math import factorial
from typing import Iterable, Sequence

import numpy as np

Permutation = tuple[int, ...]


def all_permutations(n: int) -> Iterable[Permutation]:
    return permutations(range(n))


def compose(p: Sequence[int], q: Sequence[int]) -> Permutation:
    if len(p) != len(q):
        raise ValueError("permutations must have the same length")
    return tuple(p[q_i] for q_i in q)


def inverse(p: Sequence[int]) -> Permutation:
    result = [0] * len(p)
    for i, value in enumerate(p):
        result[value] = i
    return tuple(result)


def chebyshev_distance(p: Sequence[int], q: Sequence[int]) -> int:
    if len(p) != len(q):
        raise ValueError("permutations must have the same length")
    return max((abs(a - b) for a, b in zip(p, q)), default=0)


def displacement(p: Sequence[int]) -> int:
    return max((abs(i - value) for i, value in enumerate(p)), default=0)


def generate_ball(n: int, r: int) -> Iterable[Permutation]:
    """Generate permutations p satisfying abs(p[i] - i) <= r."""
    if n < 0 or r < 0:
        raise ValueError("n and r must be nonnegative")
    used = [False] * n
    current = [0] * n

    def visit(i: int) -> Iterable[Permutation]:
        if i == n:
            yield tuple(current)
            return
        for value in range(max(0, i - r), min(n, i + r + 1)):
            if not used[value]:
                used[value] = True
                current[i] = value
                yield from visit(i + 1)
                used[value] = False

    yield from visit(0)


def band_matrix(n: int, r: int) -> np.ndarray:
    indices = np.arange(n)
    return (np.abs(indices[:, None] - indices[None, :]) <= r).astype(np.int64)


def permanent(matrix: np.ndarray) -> int:
    """Compute a square matrix permanent by Ryser's formula (exact integers)."""
    values = np.asarray(matrix, dtype=object)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("permanent requires a square matrix")
    n = values.shape[0]
    if n == 0:
        return 1
    total = 0
    for mask in range(1, 1 << n):
        product = 1
        for row in range(n):
            row_sum = sum(values[row, col] for col in range(n) if mask & (1 << col))
            product *= row_sum
        total += (-1 if (n - mask.bit_count()) % 2 else 1) * product
    return int(total)


def determinant(matrix: np.ndarray) -> int:
    """Compute the determinant exactly with fraction-free elimination."""
    values = np.asarray(matrix, dtype=object)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("determinant requires a square matrix")
    n = values.shape[0]
    work = [[int(values[i, j]) for j in range(n)] for i in range(n)]
    sign = 1
    previous_pivot = 1
    for column in range(n - 1):
        pivot_row = next((row for row in range(column, n) if work[row][column]), None)
        if pivot_row is None:
            return 0
        if pivot_row != column:
            work[column], work[pivot_row] = work[pivot_row], work[column]
            sign *= -1
        pivot = work[column][column]
        for row in range(column + 1, n):
            for col in range(column + 1, n):
                work[row][col] = (
                    work[row][col] * pivot - work[row][column] * work[column][col]
                ) // previous_pivot
            work[row][column] = 0
        previous_pivot = pivot
    return sign * (work[-1][-1] if n else 1)


def q_matrix(n: int, r: int, ball: Sequence[Permutation] | None = None) -> np.ndarray:
    members = list(generate_ball(n, r)) if ball is None else ball
    result = np.zeros((n, n), dtype=np.int64)
    for p in members:
        for i, value in enumerate(p):
            result[i, value] += 1
    return result


def adjacency_matrix(n: int, d: int) -> tuple[list[Permutation], np.ndarray]:
    """Construct a dense adjacency matrix; deliberately restricted to n <= 6."""
    if not 1 <= d <= n:
        raise ValueError("d must satisfy 1 <= d <= n")
    if n > 6:
        raise ValueError("dense adjacency is restricted to n <= 6")
    vertices = list(all_permutations(n))
    matrix = np.zeros((len(vertices), len(vertices)), dtype=np.float64)
    for i, p in enumerate(vertices):
        for j in range(i + 1, len(vertices)):
            if chebyshev_distance(p, vertices[j]) >= d:
                matrix[i, j] = matrix[j, i] = 1.0
    return vertices, matrix


def grouped_eigenvalues(values: np.ndarray, tolerance: float = 1e-8) -> list[dict[str, float | int]]:
    groups: list[dict[str, float | int]] = []
    for value in np.sort(np.asarray(values, dtype=float)):
        if groups and abs(value - float(groups[-1]["value"])) <= tolerance:
            count = int(groups[-1]["multiplicity"])
            groups[-1]["value"] = (float(groups[-1]["value"]) * count + value) / (count + 1)
            groups[-1]["multiplicity"] = count + 1
        else:
            groups.append({"value": float(value), "multiplicity": 1})
    return groups


def standard_eigenvalues(q: np.ndarray, ball_size: int) -> np.ndarray:
    values = np.linalg.eigvalsh(np.asarray(q, dtype=float))
    trivial_index = int(np.argmin(np.abs(values - ball_size)))
    return np.delete(values, trivial_index)


def experiment(n: int, d: int, include_spectrum: bool = True) -> dict[str, object]:
    if not 1 <= d <= n:
        raise ValueError("d must satisfy 1 <= d <= n")
    r = d - 1
    members = list(generate_ball(n, r))
    ball_size = len(members)
    band = band_matrix(n, r)
    permanent_value = permanent(band)
    if permanent_value != ball_size:
        raise AssertionError("ball enumeration and permanent disagree")
    q = q_matrix(n, r, members)
    if not np.array_equal(q, q.T):
        raise AssertionError("Q is not symmetric")
    if not np.all(q.sum(axis=0) == ball_size) or not np.all(q.sum(axis=1) == ball_size):
        raise AssertionError("Q margins do not equal the ball size")

    standard = standard_eigenvalues(q, ball_size)
    determinant_value = determinant(band)
    result: dict[str, object] = {
        "n": n,
        "d": d,
        "r": r,
        "factorial_n": factorial(n),
        "ball_size": ball_size,
        "degree": factorial(n) - ball_size,
        "band_determinant": determinant_value,
        "sign_adjacency_eigenvalue": -determinant_value,
        "standard_ball_eigenvalues": standard.tolist(),
        "standard_adjacency_eigenvalues": (-standard).tolist(),
    }

    if include_spectrum:
        if n > 6:
            raise ValueError("full dense spectrum is restricted to n <= 6")
        _, adjacency = adjacency_matrix(n, d)
        eigenvalues = np.linalg.eigvalsh(adjacency)
        degree = factorial(n) - ball_size
        if not np.isclose(eigenvalues.sum(), 0.0, atol=1e-7):
            raise AssertionError("adjacency trace check failed")
        if not np.isclose(eigenvalues @ eigenvalues, factorial(n) * degree, atol=1e-6):
            raise AssertionError("adjacency square-trace check failed")
        complement = np.ones_like(adjacency) - np.eye(len(adjacency)) - adjacency
        complement_values = np.linalg.eigvalsh(complement)
        tau = float(complement_values[0])
        hoffman = None
        if tau < -1e-10:
            hoffman = factorial(n) * (-tau) / ((ball_size - 1) - tau)
        result.update(
            spectrum=grouped_eigenvalues(eigenvalues),
            least_adjacency_eigenvalue=float(eigenvalues[0]),
            laplacian_gap=float(degree - eigenvalues[-2]) if len(eigenvalues) > 1 else 0.0,
            least_complement_eigenvalue=tau,
            least_ball_operator_eigenvalue=tau + 1.0,
            hoffman_clique_bound=hoffman,
        )
    return result
