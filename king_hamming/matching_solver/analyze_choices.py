#!/usr/bin/env python3
"""Measure entropy, local deltas, and ordinary compression in a bounded KHM1 prefix."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import Reader, load_dp, packed, request_count


def entropy(counts: Counter, total: int) -> float:
    return -sum((count / total) * math.log2(count / total)
                for count in counts.values()) if total else 0.0


def analyze(match_path: Path, dp_path: Path, maximum: int) -> dict:
    dp, dp_digest = load_dp(dp_path)
    size = match_path.stat().st_size
    raw = match_path.read_bytes()
    if len(raw) != size or size < 68 or hashlib.sha256(raw[:-32]).digest() != raw[-32:]:
        raise ValueError("invalid KHM1 checksum or size")
    reader = Reader(io.BytesIO(raw), size)
    if reader.take(4) != b"KHM1" or reader.take(32) != dp_digest:
        raise ValueError("wrong KHM1 dependency")
    p, r = reader.uint(), reader.uint()
    polynomial = [reader.uint() for _ in range(r + 1)]
    status, count, matched = reader.uint(), reader.uint(), reader.uint()
    if (p, r) != (dp["p"], dp["r"]) or status != 0 or count != matched or count != request_count(dp):
        raise ValueError("analysis requires a complete matching")
    bits = (dp["f"] - 1).bit_length()
    sample_count = min(count, maximum)
    choice_bytes = (count * bits + 7) // 8
    payload = raw[reader.stream.tell():reader.stream.tell() + choice_bytes]
    values = []
    for index, value in enumerate(packed(reader.stream, count, bits)):
        if index < sample_count:
            values.append(value)
    counts = Counter(values)
    deltas = Counter((values[index] - values[index - 1]) % dp["f"]
                     for index in range(1, len(values)))
    compressed = zlib.compress(payload, 1)
    return {
        "format": "KH-MATCHING-CHOICE-ANALYSIS-1",
        "file": str(match_path), "p": p, "r": r, "q": dp["q"], "f": dp["f"],
        "polynomial": polynomial, "choices": count, "sampled": sample_count,
        "bits_per_choice": bits, "distinct_sample_values": len(counts),
        "choice_entropy_bits": entropy(counts, len(values)),
        "delta_entropy_bits": entropy(deltas, len(values) - 1),
        "packed_bytes": len(payload), "zlib_level_1_bytes": len(compressed),
        "zlib_level_1_ratio": len(compressed) / len(payload) if payload else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matching", type=Path)
    parser.add_argument("--dp", required=True, type=Path)
    parser.add_argument("--max-choices", type=int, default=5_000_000)
    arguments = parser.parse_args()
    if arguments.max_choices < 1:
        parser.error("--max-choices must be positive")
    print(json.dumps(analyze(arguments.matching, arguments.dp,
                             arguments.max_choices), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
