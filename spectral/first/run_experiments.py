#!/usr/bin/env python3
"""Run Phase A--C experiments and write JSON/CSV summaries."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from spectral.chebyshev_spectra import experiment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-n", type=int, default=3)
    parser.add_argument("--max-n", type=int, default=6)
    parser.add_argument("--output", type=Path, default=Path("spectral/results"))
    parser.add_argument("--no-full-spectrum", action="store_true")
    args = parser.parse_args()
    if args.min_n < 1 or args.max_n < args.min_n:
        parser.error("require 1 <= min-n <= max-n")
    if not args.no_full_spectrum and args.max_n > 6:
        parser.error("full dense spectra are capped at n=6; use --no-full-spectrum")

    records = []
    for n in range(args.min_n, args.max_n + 1):
        for d in range(1, n + 1):
            print(f"n={n} d={d}", flush=True)
            records.append(experiment(n, d, include_spectrum=not args.no_full_spectrum))

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "phase_abc.json").write_text(json.dumps(records, indent=2) + "\n")
    scalar_keys = [
        "n", "d", "r", "factorial_n", "ball_size", "degree",
        "band_determinant", "sign_adjacency_eigenvalue",
        "least_adjacency_eigenvalue", "laplacian_gap",
        "least_complement_eigenvalue", "least_ball_operator_eigenvalue",
        "hoffman_clique_bound",
    ]
    with (args.output / "phase_abc.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=scalar_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


if __name__ == "__main__":
    main()
