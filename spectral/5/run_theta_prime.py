#!/usr/bin/env python3
"""Run theta-prime validation and the P(7,4) target."""

from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path

_module = import_module("spectral.5.theta_prime")
solve_theta_prime = _module.solve_theta_prime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", choices=["SCS", "CLARABEL"], default="SCS")
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--skip-p74", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args()
    records = []
    print("theta-prime for P(5,3)", flush=True)
    records.append(solve_theta_prime(5, 3, args.solver, args.tolerance))
    if not args.skip_p74:
        print("theta-prime for P(7,4)", flush=True)
        records.append(solve_theta_prime(7, 4, args.solver, args.tolerance))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "theta_prime_bounds.json").write_text(json.dumps(records, indent=2) + "\n")
    p74 = next((record for record in records if (record["n"], record["d"]) == (7, 4)), None)
    if p74:
        solution = dict(p74)
        solution["objective"] = solution["theta_prime_value"]
        (args.output / "p74_theta_prime_solution.json").write_text(json.dumps(solution, indent=2) + "\n")
    ordinary = {(5, 3): 9.999999984490646, (7, 4): 34.999999680346846}
    lines = ["# Schrijver theta-prime report", "",
             "| Case | Ordinary theta | Theta-prime | Integer bound | Known |",
             "|---|---:|---:|---:|---|"]
    for record in records:
        key = (record["n"], record["d"])
        lines.append(f"| P{key} | {ordinary[key]:.10g} | {record['theta_prime_value']:.10g} | {record['integer_upper_bound']} | {record['known_code_value_or_range']} |")
    lines.extend([""])
    for record in records:
        lines.extend([f"## P({record['n']},{record['d']}) diagnostics", "",
                      f"- Solver/status: {record['solver']} / {record['solver_status']}",
                      f"- Normalization residual: {record['normalization_residual']:.3g}",
                      f"- Forbidden-edge residual: {record['max_forbidden_edge_residual']:.3g}",
                      f"- Minimum PSD eigenvalue: {record['minimum_psd_eigenvalue']:.3g}",
                      f"- Minimum reconstructed f: {record['minimum_reconstructed_f_value']:.3g}",
                      f"- Nonzero blocks: `{[b['partition'] for b in record['nonzero_irrep_blocks']]}`", ""])
    (args.output / "theta_prime_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
