#!/usr/bin/env python3
"""Profile kh_gpu_dp_tile's dp_row kernel with Nsight Compute on a production-shaped interior tile.

Builds the same random full-halo tile as tests/bench.py, times one plain run, then runs `ncu`
(as root: the driver keeps GPU counters for administrators) on a few dp_row launches from the
middle of the tile and prints the report. Run it on an otherwise idle GPU.

Example: python3 tests/profile.py --field 31,7 --side 4096 --skip 2000 --count 3 --set full
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent.parent
GPU = HERE / "kh_gpu_dp_tile"


def halo_file(path: Path, p: int, side: int) -> None:
    """Random predecessor values below 2^48, as tests/bench.py makes them."""
    width = side + p * p
    data = bytearray(os.urandom(8 * width * width))
    for offset in range(7, len(data), 8):
        data[offset] = 0
        data[offset - 1] = 0
    path.write_bytes(bytes(data))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--field", default="31,7", help="p,r")
    parser.add_argument("--side", type=int, default=4096)
    parser.add_argument("--skip", type=int, default=2000, help="dp_row launches to skip (rows) before profiling")
    parser.add_argument("--count", type=int, default=3, help="dp_row launches to profile")
    parser.add_argument("--set", default="full", help="ncu section set (basic, detailed, full)")
    parser.add_argument("--report", type=Path, help="also save the ncu report (.ncu-rep) here")
    parser.add_argument("--extra", default="", help="extra ncu arguments")
    arguments = parser.parse_args()
    p, r = map(int, arguments.field.split(","))
    first = p * p + 1 + arguments.side * 3
    last = first + arguments.side - 1
    with tempfile.TemporaryDirectory(prefix="gpu-dp-profile-") as temporary:
        directory = Path(temporary)
        halo = directory / "halo.bin"
        halo_file(halo, p, arguments.side)
        base = [str(GPU), str(p), str(r), str(first), str(last), str(first), str(last), str(halo)]
        started = time.perf_counter()
        subprocess.run(base + [str(directory / "plain"), "2", str(2**31)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"plain run: {time.perf_counter() - started:.2f} s for a {arguments.side}^2 tile of {p}^{r}", flush=True)
        command = ["sudo", "-n", "ncu", "--kernel-name", "dp_row", "--launch-skip", str(arguments.skip),
                   "--launch-count", str(arguments.count), "--set", arguments.set, "--target-processes", "all"]
        if arguments.report:
            command += ["--export", str(arguments.report), "--force-overwrite"]
        command += arguments.extra.split() if arguments.extra else []
        command += base + [str(directory / "profiled"), "2", str(2**31)]
        try:
            done = subprocess.run(command, capture_output=True, text=True)
        finally:
            # ncu ran the solver as root, so its output directory is root's.
            subprocess.run(["sudo", "-n", "rm", "-rf", str(directory / "profiled")], check=False)
            if arguments.report:
                subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}",
                                str(arguments.report) + ".ncu-rep"], check=False)
        print(done.stdout)
        if done.returncode:
            print(done.stderr[-4000:], file=sys.stderr)
            return done.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
