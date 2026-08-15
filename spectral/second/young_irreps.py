"""Young orthogonal representations of symmetric groups.

A tableau is stored as ``positions[k-1] == (row, column)`` for entry ``k``.
"""

from __future__ import annotations

from functools import lru_cache
from math import factorial, sqrt
from typing import Iterable, Sequence

import numpy as np

Partition = tuple[int, ...]
Tableau = tuple[tuple[int, int], ...]


def partitions_of_n(n: int, largest: int | None = None) -> Iterable[Partition]:
    if n < 0:
        raise ValueError("n must be nonnegative")
    if n == 0:
        yield ()
        return
    upper = n if largest is None else min(n, largest)
    for first in range(upper, 0, -1):
        for rest in partitions_of_n(n - first, first):
            yield (first,) + rest


def irrep_dimension(partition: Sequence[int]) -> int:
    shape = tuple(partition)
    hooks = 1
    for row, width in enumerate(shape):
        for column in range(width):
            below = sum(column < lower_width for lower_width in shape[row + 1 :])
            hooks *= width - column + below
    return factorial(sum(shape)) // hooks


@lru_cache(maxsize=None)
def standard_tableaux(partition: Partition) -> tuple[Tableau, ...]:
    n = sum(partition)
    row_lengths = [0] * len(partition)
    positions: list[tuple[int, int]] = []
    result: list[Tableau] = []

    def visit() -> None:
        if len(positions) == n:
            result.append(tuple(positions))
            return
        for row in range(len(partition)):
            if row_lengths[row] >= partition[row]:
                continue
            new_length = row_lengths[row] + 1
            if row > 0 and new_length > row_lengths[row - 1]:
                continue
            positions.append((row, row_lengths[row]))
            row_lengths[row] = new_length
            visit()
            row_lengths[row] -= 1
            positions.pop()

    visit()
    return tuple(result)


@lru_cache(maxsize=None)
def irrep_generators(partition: Partition) -> tuple[np.ndarray, ...]:
    tableaux = standard_tableaux(partition)
    dimension = len(tableaux)
    lookup = {tableau: index for index, tableau in enumerate(tableaux)}
    generators: list[np.ndarray] = []
    for i in range(sum(partition) - 1):
        matrix = np.zeros((dimension, dimension), dtype=float)
        for column, tableau in enumerate(tableaux):
            row_i, col_i = tableau[i]
            row_j, col_j = tableau[i + 1]
            axial_distance = (col_j - row_j) - (col_i - row_i)
            diagonal = 1.0 / axial_distance
            matrix[column, column] = diagonal
            if abs(axial_distance) != 1:
                swapped = list(tableau)
                swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
                row = lookup[tuple(swapped)]
                matrix[row, column] = sqrt(1.0 - diagonal * diagonal)
        generators.append(matrix)
    return tuple(generators)


def adjacent_word(permutation: Sequence[int]) -> tuple[int, ...]:
    """Return indices j such that p = s_j1 ... s_jk."""
    n = len(permutation)
    if sorted(permutation) != list(range(n)):
        raise ValueError("not a zero-based permutation")
    current = list(range(n))
    word: list[int] = []
    for position, target in enumerate(permutation):
        location = current.index(target, position)
        while location > position:
            j = location - 1
            current[j], current[j + 1] = current[j + 1], current[j]
            word.append(j)
            location -= 1
    return tuple(word)


def irrep_matrix(partition: Partition, permutation: Sequence[int]) -> np.ndarray:
    result = np.eye(irrep_dimension(partition))
    generators = irrep_generators(tuple(partition))
    for generator_index in adjacent_word(permutation):
        result = result @ generators[generator_index]
    return result


def verify_coxeter_relations(partition: Partition, tolerance: float = 1e-10) -> None:
    generators = irrep_generators(partition)
    identity = np.eye(irrep_dimension(partition))
    for generator in generators:
        if not np.allclose(generator @ generator, identity, atol=tolerance):
            raise AssertionError(f"s_i^2 failed for {partition}")
    for i, left in enumerate(generators):
        for j, right in enumerate(generators):
            if abs(i - j) > 1 and not np.allclose(left @ right, right @ left, atol=tolerance):
                raise AssertionError(f"commutation failed for {partition}")
    for i in range(len(generators) - 1):
        left, right = generators[i], generators[i + 1]
        if not np.allclose(left @ right @ left, right @ left @ right, atol=tolerance):
            raise AssertionError(f"braid relation failed for {partition}")

