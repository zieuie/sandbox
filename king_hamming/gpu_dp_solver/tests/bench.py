#!/usr/bin/env python3
"""Time kh_gpu_dp_tile against kh_dp_tile on production-shaped interior tiles."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
GPU = HERE / "kh_gpu_dp_tile"
CPU = ROOT / "dp_solver" / "kh_dp_tile"


def run(binary: Path, p: int, r: int, first: int, side: int, halo: Path, output: Path,
        threads: int, cpus: str | None) -> float:
    command = [str(binary), str(p), str(r), str(first), str(first + side - 1), str(first),
               str(first + side - 1), str(halo), str(output), str(threads), str(2**31)]
    if cpus:
        command = ["taskset", "-c", cpus, *command]
    start = time.perf_counter()
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        epilog="Example: python3 tests/bench.py --run --field 13,9 --cpus 12,13 --threads 2")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--field", action="append", help="p,r (repeatable); default 13,9 and 23,7")
    parser.add_argument("--side", type=int, default=512)
    parser.add_argument("--threads", type=int, default=2, help="CPU kernel threads")
    parser.add_argument("--cpus", help="taskset CPU list for the CPU kernel")
    parser.add_argument("--json", type=Path)
    arguments = parser.parse_args()
    if not arguments.run:
        parser.print_help()
        return 0
    fields = [tuple(map(int, text.split(","))) for text in (arguments.field or ["13,9", "23,7"])]
    rng = random.Random(1)
    results = []
    with tempfile.TemporaryDirectory(prefix="gpu-dp-bench-") as temporary:
        directory = Path(temporary)
        for p, r in fields:
            radius = p * p
            first = radius + 1 + arguments.side * 3  # interior tile with a full halo
            width = arguments.side + radius
            halo = directory / f"halo_{p}_{r}.bin"
            halo.write_bytes(os.urandom(8 * width * width))
            # Keep values small enough to avoid wrap so the timing reflects ordinary data.
            data = bytearray(halo.read_bytes())
            for offset in range(7, len(data), 8):
                data[offset] = 0
                data[offset - 1] = 0
            halo.write_bytes(bytes(data))
            gpu_seconds = [run(GPU, p, r, first, arguments.side, halo, directory / f"g{p}{r}{i}", arguments.threads, None)
                           for i in range(3)]
            cpu_seconds = run(CPU, p, r, first, arguments.side, halo, directory / f"c{p}{r}",
                              arguments.threads, arguments.cpus)
            same = all((directory / f"g{p}{r}0" / name).read_bytes() == (directory / f"c{p}{r}" / name).read_bytes()
                       for name in ("values.bin", "choices.bin", "tile.json"))
            record = dict(field=f"{p}^{r}", side=arguments.side, cpu_threads=arguments.threads,
                          cpu_seconds=round(cpu_seconds, 3), gpu_seconds=round(min(gpu_seconds), 3),
                          speedup=round(cpu_seconds / min(gpu_seconds), 1), identical=same)
            print(json.dumps(record), flush=True)
            results.append(record)
    if arguments.json:
        with arguments.json.open("a") as stream:
            for record in results:
                stream.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
