#!/usr/bin/env python3
"""Tabulate the small atom vocabulary and residue structure of retained KHD1 optima."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dp_solver.artifacts import decode_dp


def analyze(paths: list[Path]) -> dict:
    """Return a deterministic, JSON-safe template inventory for KHD1 paths."""
    fields = []
    vocabulary = Counter()
    by_prime = defaultdict(Counter)
    for path in sorted(paths, key=lambda item: str(item)):
        document = decode_dp(path.read_bytes())
        atoms = []
        used_a = used_b = cosets = 0
        for run in document["runs"]:
            atom = (run["a"], run["b"], run["t"])
            vocabulary[atom] += 1
            by_prime[document["p"]][atom] += run["repeat"]
            copies = run["t"] * run["repeat"]
            used_a += run["a"] * copies
            used_b += run["b"] * copies
            cosets += copies
            atoms.append({**run, "swapped": [run["b"], run["a"], run["t"]]})
        fields.append({
            "file": str(path), "p": document["p"], "r": document["r"],
            "q": document["q"], "theta": document["theta"],
            "budget": document["budget"], "used_a": used_a, "used_b": used_b,
            "residue_a": document["budget"] - used_a,
            "residue_b": document["budget"] - used_b,
            "cosets": cosets, "run_count": len(atoms), "atoms": atoms,
        })
    atom_record = lambda item: {"a": item[0][0], "b": item[0][1],
                                "t": item[0][2], "count": item[1]}
    return {
        "format": "KH-DP-TEMPLATE-INVENTORY-1",
        "files": len(fields),
        "distinct_atoms": len(vocabulary),
        "vocabulary": [atom_record(item) for item in sorted(vocabulary.items())],
        "by_prime": {str(prime): [atom_record(item) for item in sorted(counts.items())]
                     for prime, counts in sorted(by_prime.items())},
        "fields": fields,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path,
                        help="write fresh JSON output instead of stdout")
    arguments = parser.parse_args()
    paths = []
    for path in arguments.paths:
        paths.extend(sorted(path.rglob("*.khdp")) if path.is_dir() else [path])
    result = json.dumps(analyze(paths), indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(result, end="")
    else:
        with arguments.output.open("x") as output:
            output.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
