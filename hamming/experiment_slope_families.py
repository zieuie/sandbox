#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json

from overlap_tools import (
    build_candidate_family,
    build_instance,
    conflict_graph_stats,
    consecutive_slopes,
    interval_design,
    randomish_slopes,
    subgroup_slopes,
    summarize_stats,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare overlap behavior for different slope families.")
    parser.add_argument("q", help="Prime power, for example 3^3 or 7^3")
    parser.add_argument("--json", action="store_true", help="Emit full JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    instance = build_instance(args.q)
    design = interval_design(instance)

    families = {
        "consecutive": consecutive_slopes(instance),
        "subgroup": subgroup_slopes(instance),
        "randomish": randomish_slopes(instance),
    }

    results = {}
    for name, slopes in families.items():
        family = build_candidate_family(instance, design, slopes)
        results[name] = {
            "slopes": slopes,
            "stats": conflict_graph_stats(instance, family, slopes),
        }

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        print(f"q={instance.field.Q} blocks={instance.block_count}")
        print("design: current interval residues")
        for name, result in results.items():
            print()
            print(f"[{name}]")
            print(f"slopes: {result['slopes']}")
            print(summarize_stats(result["stats"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
