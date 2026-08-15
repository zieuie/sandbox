"""Blockwise Phase D spectral computations."""

from __future__ import annotations

from math import factorial
from typing import Sequence

import numpy as np

from spectral.first.chebyshev_spectra import generate_ball, grouped_eigenvalues
from spectral.second.young_irreps import Partition, irrep_dimension, irrep_matrix, partitions_of_n


def ball_block(partition: Partition, ball: Sequence[tuple[int, ...]]) -> np.ndarray:
    block = np.zeros((irrep_dimension(partition),) * 2, dtype=float)
    for permutation in ball:
        block += irrep_matrix(partition, permutation)
    residual = np.max(np.abs(block - block.T)) if block.size else 0.0
    if residual > 1e-9:
        raise AssertionError(f"non-symmetric ball block for {partition}: {residual}")
    return (block + block.T) / 2.0


def block_record(partition: Partition, ball: Sequence[tuple[int, ...]]) -> dict[str, object]:
    block = ball_block(partition, ball)
    eigenvalues = np.linalg.eigvalsh(block)
    return {
        "partition": list(partition),
        "dimension": irrep_dimension(partition),
        "block_min": float(eigenvalues[0]),
        "block_max": float(eigenvalues[-1]),
        "block_trace": float(np.trace(block)),
        "eigenvalues": eigenvalues.tolist(),
    }


def phase_d_experiment(n: int, d: int, tolerance: float = 1e-8) -> dict[str, object]:
    if not 1 <= d <= n:
        raise ValueError("d must satisfy 1 <= d <= n")
    ball = list(generate_ball(n, d - 1))
    records = [block_record(shape, ball) for shape in partitions_of_n(n)]
    if sum(int(item["dimension"]) ** 2 for item in records) != factorial(n):
        raise AssertionError("sum of squared irrep dimensions is not n!")
    nontrivial = [item for item in records if item["partition"] != [n]]
    global_max = max(float(item["block_max"]) for item in nontrivial)
    global_min = min(float(item["block_min"]) for item in nontrivial)
    maximizing = [item["partition"] for item in nontrivial if abs(float(item["block_max"]) - global_max) <= tolerance]
    minimizing = [item["partition"] for item in nontrivial if abs(float(item["block_min"]) - global_min) <= tolerance]
    standard = next(item for item in records if item["partition"] == [n - 1, 1])
    tau = global_min - 1.0
    hoffman = None
    if tau < -tolerance:
        hoffman = factorial(n) * (-tau) / ((len(ball) - 1) - tau)
    return {
        "n": n, "d": d, "r": d - 1, "ball_size": len(ball),
        "partitions": records,
        "max_nontrivial_block_max": global_max,
        "maximizing_partitions": maximizing,
        "min_nontrivial_block_min": global_min,
        "minimizing_partitions": minimizing,
        "standard_block_min": standard["block_min"],
        "standard_block_max": standard["block_max"],
        "least_adjacency_eigenvalue": -global_max,
        "least_complement_eigenvalue": tau,
        "hoffman_clique_bound": hoffman,
    }


def reconstructed_adjacency_eigenvalues(record: dict[str, object]) -> np.ndarray:
    n = int(record["n"])
    values = [float(factorial(n) - int(record["ball_size"]))]
    for block in record["partitions"]:
        if block["partition"] != [n]:
            for eigenvalue in block["eigenvalues"]:
                values.extend([-float(eigenvalue)] * int(block["dimension"]))
    return np.sort(np.asarray(values))


def reconstructed_spectrum(record: dict[str, object]) -> list[dict[str, float | int]]:
    return grouped_eigenvalues(reconstructed_adjacency_eigenvalues(record))


def compare_direct_spectrum(record: dict[str, object], direct: dict[str, object], tolerance: float = 1e-8) -> None:
    direct_values: list[float] = []
    for group in direct["spectrum"]:
        direct_values.extend([float(group["value"])] * int(group["multiplicity"]))
    reconstructed = reconstructed_adjacency_eigenvalues(record)
    expected = np.sort(np.asarray(direct_values))
    if reconstructed.shape != expected.shape or not np.allclose(reconstructed, expected, atol=tolerance):
        difference = float(np.max(np.abs(reconstructed - expected))) if reconstructed.shape == expected.shape else None
        raise AssertionError(f"reconstructed spectrum differs from direct spectrum: {difference}")


def fibonacci(n: int) -> int:
    if n < 0:
        raise ValueError("n must be nonnegative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def d2_q_matrix(n: int) -> np.ndarray:
    result = np.zeros((n, n), dtype=object)
    for index in range(n):
        i = index + 1
        result[index, index] = fibonacci(i) * fibonacci(n - i + 1)
        if index + 1 < n:
            value = fibonacci(i) * fibonacci(n - i)
            result[index, index + 1] = result[index + 1, index] = value
    return result


def d2_band_determinant(n: int) -> int:
    if n == 0:
        return 1
    before, current = 1, 1
    for _ in range(2, n + 1):
        before, current = current, current - before
    return current


def dn1_candidate_spectrum(n: int) -> list[dict[str, int]]:
    if n < 4:
        raise ValueError("candidate is stated for n >= 4")
    scale = factorial(n - 2)
    pairs = [
        ((2 * n - 3) * scale, 1), (-factorial(n - 1), n - 1),
        ((n - 3) * scale, n - 1), (-scale, n * (n - 3) // 2),
        (scale, (n - 1) * (n - 2) // 2), (0, factorial(n) - n * (n - 1)),
    ]
    combined: dict[int, int] = {}
    for value, multiplicity in pairs:
        combined[value] = combined.get(value, 0) + multiplicity
    return [{"value": value, "multiplicity": combined[value]} for value in sorted(combined) if combined[value]]

