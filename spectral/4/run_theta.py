#!/usr/bin/env python3
"""Run unrestricted weighted Hoffman and Fourier theta experiments."""

from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path

_theta = import_module("spectral.4.theta")
solve_theta = _theta.solve_theta
solve_unrestricted_weighted = _theta.solve_unrestricted_weighted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", choices=["CLARABEL", "SCS"], default="SCS")
    parser.add_argument("--skip-p74", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "results")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    print("unrestricted weighted Hoffman for P(5,3)", flush=True)
    unrestricted = solve_unrestricted_weighted()
    (args.output / "p53_unrestricted_weighted.json").write_text(json.dumps(unrestricted, indent=2) + "\n")

    theta_records = []
    print("theta for P(5,3)", flush=True)
    theta_records.append(solve_theta(5, 3, solver=args.solver))
    if not args.skip_p74:
        print("theta for P(7,4)", flush=True)
        theta_records.append(solve_theta(7, 4, solver=args.solver))
    (args.output / "theta_bounds.json").write_text(json.dumps(theta_records, indent=2) + "\n")

    lines = ["# Fourier-domain Lovász theta report", "", "## Unrestricted weighted Hoffman: P(5,3)", "",
             f"- Variables: {unrestricted['number_of_variables']}",
             f"- Row sum: {unrestricted['row_sum']:.12g}",
             f"- Real bound: {unrestricted['spectral_upper_bound_real']:.12g}",
             f"- Integer bound: {unrestricted['spectral_upper_bound_floor']}",
             f"- Active partitions: `{unrestricted['active_partitions']}`", ""]
    for record in theta_records:
        lines.extend([f"## Theta: P({record['n']},{record['d']})", "",
                      f"- Theta value: {record['theta_value']:.12g}",
                      f"- Integer upper bound: {record['integer_upper_bound']}",
                      f"- Solver/status: {record['solver']} / {record['solver_status']}",
                      f"- Maximum residual: {record['max_constraint_residual']:.3g}",
                      f"- Minimum PSD eigenvalue: {record['minimum_psd_eigenvalue']:.3g}",
                      f"- Nonzero blocks: `{[b['partition'] for b in record['nonzero_blocks']]}`", ""])
    (args.output / "theta_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
