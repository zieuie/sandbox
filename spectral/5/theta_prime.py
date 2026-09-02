"""Fourier-domain Schrijver theta-prime for Chebyshev Cayley graphs."""

from __future__ import annotations

from itertools import permutations
from math import factorial

import cvxpy as cp
import numpy as np

from spectral.first.chebyshev_spectra import compose, displacement, inverse
from spectral.second.young_irreps import irrep_dimension, irrep_matrix, partitions_of_n


def inverse_representatives(permutations_to_reduce):
    remaining = set(permutations_to_reduce)
    representatives = []
    while remaining:
        permutation = min(remaining)
        orbit = {permutation, inverse(permutation)}
        representatives.append(permutation)
        remaining.difference_update(orbit)
    return representatives


def symmetric_irrep_matrix(shape, permutation):
    matrix = irrep_matrix(shape, permutation)
    return (matrix + matrix.T) / 2.0


def solve_theta_prime(n: int, d: int, solver: str = "SCS", tolerance: float = 1e-6):
    group = list(permutations(range(n)))
    identity = tuple(range(n))
    forbidden = [p for p in group if 0 < displacement(p) < d]
    allowed_nonidentity = [p for p in group if displacement(p) >= d]
    forbidden_reps = inverse_representatives(forbidden)
    nonnegative_reps = inverse_representatives(allowed_nonidentity)
    shapes = list(partitions_of_n(n))
    dimensions = {shape: irrep_dimension(shape) for shape in shapes}
    variables = {shape: cp.Variable((dimensions[shape], dimensions[shape]), symmetric=True) for shape in shapes}
    constraints = [variables[shape] >> 0 for shape in shapes]
    constraints.append(sum(dimensions[shape] * cp.trace(variables[shape]) for shape in shapes) == factorial(n))

    def fourier_expression(permutation):
        return sum(
            dimensions[shape] * cp.sum(cp.multiply(variables[shape], symmetric_irrep_matrix(shape, permutation)))
            for shape in shapes
        )

    constraints.extend(fourier_expression(p) == 0 for p in forbidden_reps)
    constraints.extend(fourier_expression(p) >= 0 for p in nonnegative_reps)
    problem = cp.Problem(cp.Maximize(variables[(n,)][0, 0]), constraints)
    options = {"verbose": False}
    if solver.upper() == "SCS":
        options.update(eps=tolerance, max_iters=250_000)
    elif solver.upper() == "CLARABEL":
        options.update(tol_gap_abs=tolerance, tol_gap_rel=tolerance, tol_feas=tolerance, max_iter=1000)
    value = problem.solve(solver=solver.upper(), **options)
    if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
        raise RuntimeError(f"theta-prime solve failed with status {problem.status}")

    matrices = {shape: np.asarray(variables[shape].value, dtype=float) for shape in shapes}

    def numerator(permutation):
        return float(sum(
            dimensions[shape] * np.sum(matrices[shape] * symmetric_irrep_matrix(shape, permutation))
            for shape in shapes
        ))

    numerators = {p: numerator(p) for p in group}
    f_values = {p: value / factorial(n) for p, value in numerators.items()}
    normalization = sum(dimensions[shape] * np.trace(matrices[shape]) for shape in shapes)
    minimum_psd = min(float(np.linalg.eigvalsh(matrix)[0]) for matrix in matrices.values())
    forbidden_residual = max((abs(numerators[p]) for p in forbidden), default=0.0)
    minimum_f = min(f_values.values())
    minimum_elements = [list(p) for p, result in f_values.items() if result <= minimum_f + 1e-8]
    theta_value = float(value)
    near_integer = round(theta_value)
    integer_bound = int(near_integer if abs(theta_value - near_integer) <= 1e-4 else np.floor(theta_value))
    nonzero = [
        {"partition": list(shape), "dimension": dimensions[shape],
         "trace": float(np.trace(matrices[shape])), "frobenius_norm": float(np.linalg.norm(matrices[shape]))}
        for shape in shapes if np.linalg.norm(matrices[shape]) > 1e-7
    ]
    return {
        "n": n,
        "d": d,
        "theta_prime_value": theta_value,
        "integer_upper_bound": integer_bound,
        "integer_snap_tolerance": 1e-4,
        "known_code_value_or_range": {"5,3": "P(5,3)=10", "7,4": "33<=P(7,4)<=35"}.get(f"{n},{d}"),
        "solver": solver.upper(),
        "solver_status": problem.status,
        "normalization_residual": abs(float(normalization) - factorial(n)),
        "max_forbidden_edge_residual": forbidden_residual,
        "minimum_psd_eigenvalue": minimum_psd,
        "minimum_reconstructed_f_value": minimum_f,
        "max_nonnegativity_violation": max(0.0, -minimum_f),
        "number_of_forbidden_constraints": len(forbidden_reps),
        "number_of_nonnegativity_constraints": len(nonnegative_reps),
        "number_of_group_elements_checked": len(group),
        "nonzero_irrep_blocks": nonzero,
        "fourier_block_matrices": {
            str(list(shape)): matrices[shape].tolist() for shape in shapes
        },
        "elements_attaining_minimum_f": minimum_elements,
    }


def reconstruct_f(n: int, matrices, permutation):
    """Reconstruct f(g) from numeric Fourier block matrices."""
    total = 0.0
    for shape in partitions_of_n(n):
        total += irrep_dimension(shape) * np.sum(np.asarray(matrices[shape]) * symmetric_irrep_matrix(shape, permutation))
    return float(total / factorial(n))


def invariant_primal_matrix(n: int, f_values):
    """Return X[u,v] = f(v o u^-1)/n! under the repository convention."""
    group = list(permutations(range(n)))
    matrix = np.zeros((factorial(n), factorial(n)))
    for row, u in enumerate(group):
        u_inverse = inverse(u)
        for column, v in enumerate(group):
            matrix[row, column] = f_values[compose(v, u_inverse)] / factorial(n)
    return group, matrix
