"""Pure deterministic enumeration of primitive generator-X field polynomials."""

from __future__ import annotations

from matching_solver.artifacts import primitive


def next_primitive(p: int, r: int, q: int,
                   prior: list[int]) -> list[int] | None:
    """Return the next primitive monic polynomial after prior in packed order."""
    start = 1 + sum(value * p**index for index, value in enumerate(prior[:-1]))
    for packed in range(start, q):
        if packed % p == 0:
            continue
        value = packed
        coefficients = []
        for _ in range(r):
            coefficients.append(value % p)
            value //= p
        polynomial = coefficients + [1]
        if primitive(p, r, polynomial):
            return polynomial
    return None


def first_primitive(p: int, r: int, q: int) -> list[int]:
    """Return the first primitive monic polynomial in packed order."""
    polynomial = next_primitive(p, r, q, [0] * r + [1])
    if polynomial is None:
        raise ValueError(f"no primitive polynomial found for {p}^{r}")
    return polynomial
