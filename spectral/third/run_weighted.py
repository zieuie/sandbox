#!/usr/bin/env python3
"""Run structured weighted-Hoffman experiments."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from spectral.third.weighted_sdp import build_problem, ordinary_hoffman, solve_weighted_problem


KNOWN = {(5, 3): "P(5,3)=10", (6, 3): "P(6,3)=20", (7, 4): "33<=P(7,4)<=35",
         (7, 3): "100<=P(7,3)<=105", (8, 4): "P(8,4)=70"}


def main() -> None:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", default=["5,3", "7,4"], help="pairs such as 5,3 7,4")
    parser.add_argument("--families", nargs="+", choices=["shell", "shell_cycle_type"], default=["shell", "shell_cycle_type"])
    parser.add_argument("--output", type=Path, default=base / "results")
    parser.add_argument("--tolerance", type=float, default=1e-8)
    args = parser.parse_args()
    cases = [tuple(map(int, value.split(","))) for value in args.cases]
    records = []
    for n, d in cases:
        for family in args.families:
            print(f"P({n},{d}) family={family}", flush=True)
            problem = build_problem(n, d, family)
            record = solve_weighted_problem(problem, tolerance=args.tolerance)
            record["ordinary_hoffman"] = ordinary_hoffman(problem)
            record["known_benchmark"] = KNOWN.get((n, d))
            if (n, d) == (7, 4):
                integer_bound = record["spectral_upper_bound_floor"]
                record["benchmark_outcome"] = (
                    "proves P(7,4)=33" if integer_bound <= 33 else
                    "improves upper bound to 34" if integer_bound == 34 else
                    "does not improve known upper bound 35"
                )
            else:
                record["benchmark_outcome"] = None
            records.append(record)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "weighted_bounds.json").write_text(json.dumps(records, indent=2) + "\n")
    fields = ["n", "d", "weight_family", "number_of_variables", "row_sum",
              "spectral_upper_bound_real", "spectral_upper_bound_floor", "active_partitions",
              "minimum_block_eigenvalue", "solver", "solver_tolerance", "iterations",
              "known_benchmark", "benchmark_outcome"]
    with (args.output / "weighted_bounds.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    p74 = [record for record in records if (record["n"], record["d"]) == (7, 4)]
    if p74:
        best = min(p74, key=lambda record: record["spectral_upper_bound_real"])
        (args.output / "p74_best_weights.json").write_text(json.dumps(best, indent=2) + "\n")
    lines = ["# Weighted spectral report", ""]
    for record in records:
        lines.extend([
            f"## P({record['n']},{record['d']}), {record['weight_family']}", "",
            f"- Variables: {record['number_of_variables']}",
            f"- Row sum: {record['row_sum']:.12g}",
            f"- Real bound: {record['spectral_upper_bound_real']:.12g}",
            f"- Integer bound: {record['spectral_upper_bound_floor']}",
            f"- Ordinary Hoffman bound: {record['ordinary_hoffman']['real_bound']:.12g}",
            f"- Active partitions: `{record['active_partitions']}`", "",
        ])
        if record["benchmark_outcome"]:
            lines.insert(len(lines) - 1, f"- Benchmark outcome: **{record['benchmark_outcome']}**")
    (args.output / "weighted_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
