"""Fourier-domain Lovasz theta for Chebyshev forbidden Cayley graphs."""

from __future__ import annotations

from math import factorial, floor
from typing import Sequence

import cvxpy as cp
import numpy as np

from spectral.first.chebyshev_spectra import inverse
from spectral.second.young_irreps import irrep_dimension, irrep_matrix, partitions_of_n
from spectral.third.weighted_sdp import (
    WeightedProblem,
    forbidden_permutations,
    ordinary_hoffman,
    solve_weighted_problem,
)


def numerical_integer_upper_bound(value: float, snap_tolerance: float = 1e-5) -> int:
    """Floor a bound, snapping solver-scale near-integers to that integer."""
    nearest = round(value)
    return int(nearest if abs(value - nearest) <= snap_tolerance else floor(value))


def inverse_orbits(permutations: Sequence[tuple[int, ...]]) -> list[list[tuple[int, ...]]]:
    remaining = set(permutations)
    orbits = []
    while remaining:
        permutation = min(remaining)
        inverse_permutation = inverse(permutation)
        orbit = sorted({permutation, inverse_permutation})
        remaining.difference_update(orbit)
        orbits.append(orbit)
    return orbits


def unrestricted_weighted_problem(n: int, d: int) -> WeightedProblem:
    orbits = inverse_orbits(forbidden_permutations(n, d))
    shapes = [shape for shape in partitions_of_n(n) if shape != (n,)]
    blocks = {}
    for shape in shapes:
        shape_blocks = []
        for orbit in orbits:
            matrix = sum((irrep_matrix(shape, p) for p in orbit), np.zeros((irrep_dimension(shape),) * 2))
            shape_blocks.append((matrix + matrix.T) / 2.0)
        blocks[shape] = shape_blocks
    return WeightedProblem(
        n=n,
        d=d,
        family="inverse_pair",
        keys=[tuple(orbit) for orbit in orbits],
        sizes=np.asarray([len(orbit) for orbit in orbits], dtype=float),
        partitions=shapes,
        blocks=blocks,
    )


def solve_unrestricted_weighted(n: int = 5, d: int = 3, tolerance: float = 1e-8) -> dict[str, object]:
    problem = unrestricted_weighted_problem(n, d)
    result = solve_weighted_problem(problem, tolerance=tolerance, max_iterations=2000)
    result["ordinary_hoffman"] = ordinary_hoffman(problem)
    result["solver_status"] = "optimal_numerical"
    return result


def solve_theta(
    n: int,
    d: int,
    solver: str = "CLARABEL",
    tolerance: float = 1e-8,
) -> dict[str, object]:
    """Solve the symmetry-reduced theta SDP using one PSD variable per irrep."""
    shapes = list(partitions_of_n(n))
    dimensions = {shape: irrep_dimension(shape) for shape in shapes}
    variables = {
        shape: cp.Variable((dimensions[shape], dimensions[shape]), symmetric=True)
        for shape in shapes
    }
    constraints = [variables[shape] >> 0 for shape in shapes]
    constraints.append(
        sum(dimensions[shape] * cp.trace(variables[shape]) for shape in shapes) == factorial(n)
    )
    forbidden = forbidden_permutations(n, d)
    representatives = [orbit[0] for orbit in inverse_orbits(forbidden)]
    representation_cache = {}
    for permutation in representatives:
        expression = 0
        for shape in shapes:
            matrix = irrep_matrix(shape, permutation)
            symmetric_matrix = (matrix + matrix.T) / 2.0
            representation_cache[(shape, permutation)] = symmetric_matrix
            expression += dimensions[shape] * cp.sum(cp.multiply(variables[shape], symmetric_matrix))
        constraints.append(expression == 0)
    problem = cp.Problem(cp.Maximize(variables[(n,)][0, 0]), constraints)
    options = {"verbose": False}
    if solver.upper() == "CLARABEL":
        options.update(tol_gap_abs=tolerance, tol_gap_rel=tolerance, tol_feas=tolerance, max_iter=500)
    elif solver.upper() == "SCS":
        options.update(eps=tolerance, max_iters=200_000)
    value = problem.solve(solver=solver.upper(), **options)
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
        raise RuntimeError(f"theta solve failed with status {problem.status}")

    matrices = {shape: np.asarray(variables[shape].value, dtype=float) for shape in shapes}
    normalization = sum(dimensions[shape] * np.trace(matrices[shape]) for shape in shapes)
    edge_residuals = []
    for permutation in representatives:
        edge_residuals.append(abs(sum(
            dimensions[shape] * np.sum(matrices[shape] * representation_cache[(shape, permutation)])
            for shape in shapes
        )))
    minimum_psd = min(float(np.linalg.eigvalsh(matrix)[0]) for matrix in matrices.values())
    nonzero = [
        {"partition": list(shape), "dimension": dimensions[shape],
         "trace": float(np.trace(matrices[shape])), "frobenius_norm": float(np.linalg.norm(matrices[shape]))}
        for shape in shapes if np.linalg.norm(matrices[shape]) > 1e-7
    ]
    theta = float(value)
    return {
        "n": n,
        "d": d,
        "theta_value": theta,
        "integer_upper_bound": numerical_integer_upper_bound(theta),
        "integer_snap_tolerance": 1e-5,
        "known_code_value_or_range": {"5,3": "P(5,3)=10", "7,4": "33<=P(7,4)<=35"}.get(f"{n},{d}"),
        "solver": solver.upper(),
        "solver_status": problem.status,
        "number_of_irrep_blocks": len(shapes),
        "number_of_forbidden_permutations": len(forbidden),
        "number_of_edge_constraints_after_inversion": len(representatives),
        "normalization_residual": abs(float(normalization) - factorial(n)),
        "max_edge_constraint_residual": max(edge_residuals, default=0.0),
        "max_constraint_residual": max(abs(float(normalization) - factorial(n)), max(edge_residuals, default=0.0), max(0.0, -minimum_psd)),
        "minimum_psd_eigenvalue": minimum_psd,
        "nonzero_blocks": nonzero,
    }
