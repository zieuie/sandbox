#!/usr/bin/env python3
"""Independently verify a KHDP2-draft split and its exact DP optimum."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# Construct a useful no-argument verifier interface.
def build_parser() -> argparse.ArgumentParser:
    """Build and return the independent verifier parser."""

    parser = argparse.ArgumentParser(
        description="Independently recompute and verify a KHDP2-draft artifact.",
        epilog="Example: ./verify_dp.py /tmp/split_5_3.khdp.json --max-visits 1000000",
    )
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--max-visits", type=int, default=200_000_000)
    return parser


# Determine primality without sharing code with the C solver.
def is_prime(value: int) -> bool:
    """Return whether value is prime."""

    if value < 2:
        return False

    divisor = 2

    while divisor * divisor <= value:
        if value % divisor == 0:
            return False

        divisor += 1

    return True


# Evaluate the paper's residue-difference count directly with a Python set.
def omega(p: int, a: int, b: int, t: int) -> int:
    """Return the number of residues h*t-g modulo p."""

    return len({(h * t - g) % p for g in range(a) for h in range(b)})


# Expand and structurally validate run-length encoded choices.
def expand_runs(document: dict[str, Any], p: int, budget: int) -> list[tuple[int, int, int]]:
    """Return the ordered split encoded by document's run records."""

    runs = document.get("runs")

    if not isinstance(runs, list):
        raise ValueError("runs must be an array")

    steps: list[tuple[int, int, int]] = []

    for run in runs:
        if not isinstance(run, dict) or set(run) != {"a", "b", "t", "repeat"}:
            raise ValueError("run has unexpected fields")

        a = int(run["a"])
        b = int(run["b"])
        t = int(run["t"])
        repeat = int(run["repeat"])

        if not (1 <= a <= p and 1 <= b <= p and 1 <= t <= p and repeat > 0):
            raise ValueError("run value is out of range")

        if steps and steps[-1] == (a, b, t):
            raise ValueError("adjacent identical runs are not maximally compressed")

        steps.extend([(a, b, t)] * repeat)

        if len(steps) > budget:
            raise ValueError("split contains too many steps")

    return steps


# Recompute Figure 1 with its exact iteration and strict tie rule.
def recompute(p: int, budget: int, max_visits: int) -> tuple[int, list[tuple[int, int, int]]]:
    """Return the exact optimum and tie-selected ordered split."""

    visits = budget * budget * p * p * p

    if visits > max_visits:
        raise ValueError(f"verification needs {visits} transition visits, above --max-visits")

    side = budget + 1
    values = [0] * (side * side)
    choices: list[tuple[int, int, int] | None] = [None] * (side * side)
    gains = {
        (a, b, t): t * omega(p, a, b, t)
        for a in range(1, p + 1)
        for b in range(1, p + 1)
        for t in range(1, p + 1)
    }

    for u in range(1, budget + 1):
        for v in range(1, budget + 1):
            cell = u * side + v

            for a in range(1, p + 1):
                for b in range(1, p + 1):
                    for t in range(1, p + 1):
                        delta_u = a * t
                        delta_v = b * t

                        if delta_u > u or delta_v > v:
                            continue

                        candidate = gains[(a, b, t)] + values[(u - delta_u) * side + v - delta_v]

                        if candidate > values[cell]:
                            values[cell] = candidate
                            choices[cell] = (a, b, t)

    steps: list[tuple[int, int, int]] = []
    u = budget
    v = budget

    while choices[u * side + v] is not None:
        a, b, t = choices[u * side + v]
        steps.append((a, b, t))
        u -= a * t
        v -= b * t

    return values[-1], steps


# Validate dimensions, feasibility, gain, optimum, and exact tie selection.
def verify(document: dict[str, Any], max_visits: int) -> None:
    """Raise ValueError unless document is the exact expected DP result."""

    expected_fields = {"format", "p", "r", "q", "f", "budget", "theta", "runs"}

    if set(document) != expected_fields or document["format"] != "KHDP2-draft":
        raise ValueError("unsupported or malformed artifact")

    p = int(document["p"])
    r = int(document["r"])

    if not is_prime(p) or r < 3 or r % 2 == 0:
        raise ValueError("invalid prime-power parameters")

    q = p**r
    f = p ** (r // 2)
    budget = p * f

    if q > 2**64 - 1 or budget > 2**32 - 1:
        raise ValueError("DP dimensions exceed native integer width")

    if (document["q"], document["f"], document["budget"]) != (q, f, budget):
        raise ValueError("derived dimensions do not match")

    steps = expand_runs(document, p, budget)
    used_u = sum(a * t for a, _, t in steps)
    used_v = sum(b * t for _, b, t in steps)
    gain = sum(t * omega(p, a, b, t) for a, b, t in steps)

    if used_u > budget or used_v > budget:
        raise ValueError("split exceeds a budget")

    if gain != int(document["theta"]):
        raise ValueError("split gain does not equal theta")

    optimum, selected = recompute(p, budget, max_visits)

    if optimum != int(document["theta"]):
        raise ValueError("theta is not optimal")

    if selected != steps:
        raise ValueError("split does not match the paper's tie rule")


# Load and independently verify one artifact.
def main() -> int:
    """Verify one artifact and return an exit status."""

    parser = build_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    arguments = parser.parse_args()

    try:
        raw = arguments.artifact.read_bytes()
        if raw.startswith(b"KHD1"):
            from artifacts import decode_dp
            document = decode_dp(raw)
        else:
            document = json.loads(raw)

        if not isinstance(document, dict):
            raise ValueError("artifact root must be an object")

        verify(document, arguments.max_visits)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"verify_dp.py: {error}", file=sys.stderr)
        return 1

    print(f"verified exact DP artifact: {arguments.artifact}")
    return 0


# Enter through a small testable main function.
if __name__ == "__main__":
    raise SystemExit(main())
