#!/usr/bin/env python3
"""Run the Phase D irreducible-block scan."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from spectral.second.phase_d import compare_direct_spectrum, phase_d_experiment


def load_direct_records(path: Path) -> dict[tuple[int, int], dict[str, object]]:
    if not path.exists():
        return {}
    return {(int(row["n"]), int(row["d"])): row for row in json.loads(path.read_text())}


def main() -> None:
    spectral_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-n", type=int, default=3)
    parser.add_argument("--max-n", type=int, default=5)
    parser.add_argument("--min-d", type=int)
    parser.add_argument("--max-d", type=int)
    parser.add_argument("--output", type=Path, default=spectral_dir / "second/results")
    parser.add_argument("--phase-abc", type=Path, default=spectral_dir / "first/spectral/results/phase_abc.json")
    args = parser.parse_args()
    if args.min_n < 2 or args.max_n < args.min_n:
        parser.error("require 2 <= min-n <= max-n")
    direct = load_direct_records(args.phase_abc)
    records = []
    for n in range(args.min_n, args.max_n + 1):
        for d in range(max(1, args.min_d or 1), min(n, args.max_d or n) + 1):
            print(f"n={n} d={d}", flush=True)
            record = phase_d_experiment(n, d)
            prior = direct.get((n, d))
            if prior and "spectrum" in prior:
                compare_direct_spectrum(record, prior)
                record["phase_abc_spectrum_verified"] = True
            else:
                record["phase_abc_spectrum_verified"] = False
            records.append(record)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "phase_d_blocks.json").write_text(json.dumps(records, indent=2) + "\n")
    fields = ["n", "d", "r", "ball_size", "standard_block_min", "standard_block_max",
              "min_nontrivial_block_min", "max_nontrivial_block_max", "minimizing_partitions",
              "maximizing_partitions", "least_adjacency_eigenvalue", "least_complement_eigenvalue",
              "hoffman_clique_bound", "phase_abc_spectrum_verified"]
    with (args.output / "phase_d_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


if __name__ == "__main__":
    main()
