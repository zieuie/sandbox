#!/usr/bin/env python3
"""Check kh_gpu_dp_tile produces byte-identical tiles to kh_dp_tile on random halos."""

from __future__ import annotations

import argparse
import filecmp
from pathlib import Path
import random
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
GPU = HERE / "kh_gpu_dp_tile"
CPU = ROOT / "dp_solver" / "kh_dp_tile"


def budget(p: int, r: int) -> int:
    return p * p ** (r // 2)


def halo(rng: random.Random, path: Path, rows: int, columns: int, style: str) -> None:
    """Random predecessor values; 'ties' uses a tiny range so equal candidates are common."""
    if style == "ties":
        values = [rng.randrange(4) for _ in range(rows * columns)]
    elif style == "zero":
        values = [0] * (rows * columns)
    elif style == "wrap":
        values = [rng.randrange(2**64 - 2**20, 2**64) for _ in range(rows * columns)]
    else:
        values = [rng.randrange(2**40) for _ in range(rows * columns)]
    path.write_bytes(b"".join(value.to_bytes(8, "little") for value in values))


def case(rng: random.Random, directory: Path, index: int, p: int, r: int, style: str) -> None:
    b = budget(p, r)
    radius = p * p
    side = rng.choice([1, 3, 7, 32, 33, 64, 100])
    first_u = rng.randint(1, b)
    first_v = rng.randint(1, b)
    last_u = min(b, first_u + rng.randint(0, side))
    last_v = min(b, first_v + rng.randint(0, side))
    origin_u = max(0, first_u - radius)
    origin_v = max(0, first_v - radius)
    rows, columns = last_u - origin_u + 1, last_v - origin_v + 1
    halo_path = directory / f"halo{index}.bin"
    halo(rng, halo_path, rows, columns, style)
    outputs = []
    for binary in (CPU, GPU):
        output = directory / f"{binary.name}{index}"
        arguments = [str(binary), str(p), str(r), str(first_u), str(last_u), str(first_v), str(last_v),
                     str(halo_path), str(output), "2", str(2**31)]
        completed = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if completed.returncode != 0:
            raise AssertionError(f"{binary.name} failed: {completed.stderr}")
        outputs.append(output)
    for name in ("values.bin", "choices.bin", "tile.json"):
        if not filecmp.cmp(outputs[0] / name, outputs[1] / name, shallow=False):
            raise AssertionError(f"{name} differs for p={p} r={r} u={first_u}..{last_u} "
                                 f"v={first_v}..{last_v} style={style}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog="Example: python3 tests/check.py --run")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--cases", type=int, default=48)
    arguments = parser.parse_args()
    if not arguments.run:
        parser.print_help()
        return 0
    if not CPU.exists():
        raise SystemExit("build dp_solver first: make -C king_hamming/dp_solver")
    rng = random.Random(arguments.seed)
    shapes = [(2, 3), (3, 3), (5, 3), (7, 3), (2, 5), (3, 5), (13, 3), (23, 3), (11, 5)]
    styles = ["random", "ties", "zero", "wrap"]
    with tempfile.TemporaryDirectory(prefix="gpu-dp-check-") as temporary:
        for index in range(arguments.cases):
            p, r = shapes[index % len(shapes)]
            case(rng, Path(temporary), index, p, r, styles[index % len(styles)])
    print(f"ok {arguments.cases} random tiles byte-identical to kh_dp_tile (values, choices, tile.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
