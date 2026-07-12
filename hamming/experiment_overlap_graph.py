#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json

from overlap_tools import build_candidate_family, build_instance, conflict_graph_stats, interval_design, summarize_stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze the current overlap graph and quotient it by slope ratio.")
    parser.add_argument("q", help="Prime power, for example 3^3 or 7^3")
    parser.add_argument("--json", action="store_true", help="Emit full JSON instead of a text summary")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    instance = build_instance(args.q)
    slopes = list(range(1, instance.block_count + 1))
    family = build_candidate_family(instance, interval_design(instance), slopes)
    stats = conflict_graph_stats(instance, family, slopes)

    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        print(f"q={instance.field.Q} blocks={instance.block_count} suffix_count={instance.suffix_count}")
        print("design: current interval residues")
        print("slopes: consecutive")
        print(summarize_stats(stats))
        print("ratio classes:")
        for ratio, ratio_info in stats["ratio_summary"].items():
            print(
                f"  ratio={ratio}: pairs={ratio_info['pairs_with_overlap']} "
                f"hist={ratio_info['intersection_hist']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
