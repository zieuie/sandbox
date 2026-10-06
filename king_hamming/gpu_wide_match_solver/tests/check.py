#!/usr/bin/env python3
"""Check kh_gpu_wide_kernel against the KHM1 verifier and brute-force matching oracles.

Adapted from gpu_block_match_solver/tests/check.py: the same fixtures and oracles, plus forced
small row budgets so that every case runs in several passes, carries requests between passes and
needs rescue passes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import header, load_dp, publish, verify  # noqa: E402

KERNEL = HERE / "kh_gpu_wide_kernel"


def field_parameters(p: int, r: int) -> dict:
    """q = p^r, F = p^floor(r/2), B = pF (see dp_solver/DESIGN.md)."""
    f = p ** (r // 2)
    return {"q": p ** r, "f": f, "budget": p * f}


def write_blocks(path: Path, runs: list[tuple[int, int]]) -> None:
    path.write_text(f"{len(runs)}\n" + "".join(f"{a} {copies}\n" for a, copies in runs))


def run_kernel(kernel: Path, p: int, r: int, blocks: Path, payload: Path, *extra: str) -> tuple[int, dict]:
    completed = subprocess.run([str(kernel), str(p), str(r), str(blocks), str(payload), *extra],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    metadata = None
    for line in completed.stdout.splitlines():
        record = json.loads(line)
        if "polynomial" in record and "status" in record:
            metadata = record
    if completed.returncode not in (0, 2, 4) or metadata is None:
        raise AssertionError(f"{kernel.name} failed ({completed.returncode}): {completed.stderr[-2000:]}")
    metadata["stderr"] = completed.stderr
    return completed.returncode, metadata


def neighbors(cells: list[int], q: int, f: int, coset: int, cell: int) -> list[int]:
    result = []
    for k in range(f):
        label = cells[cell * f + k]
        result.append(0 if label == 0 else 1 + (label - 1 + q - 1 - coset) % (q - 1))
    return result


def requests(runs: list[tuple[int, int]], f: int):
    coset = 0
    for a, copies in runs:
        for _ in range(copies):
            for cell in range(a * f):
                yield coset, cell
            coset += 1


def maximum_matching(adjacency: list[list[int]]) -> int:
    """Kuhn's augmenting-path algorithm; tiny graphs only."""
    owner: dict[int, int] = {}

    def augment(u: int, seen: set[int]) -> bool:
        for v in adjacency[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in owner or augment(owner[v], seen):
                owner[v] = u
                return True
        return False

    sys.setrecursionlimit(100000)
    return sum(augment(u, set()) for u in range(len(adjacency)))


def unpack(data: bytes, count: int, bits: int, offset_bits: int = 0) -> list[int]:
    value = int.from_bytes(data, "little")
    mask = (1 << bits) - 1
    return [(value >> (offset_bits + index * bits)) & mask for index in range(count)]



def rows_bytes(p: int, r: int, threads: int, split_bits: int, cells: int) -> int:
    """--row-bytes for a pass of `cells` rows; mirrors fw_rows_bytes() in src/field_walk.c."""
    params = field_parameters(p, r)
    nbp = (params["q"] - 1) >> split_bits
    chunks = 2 * threads + nbp + 1
    return 4 * params["budget"] + cells * (4 * (params["f"] + nbp) + 4 * chunks)


def blocks_fixture(path: Path, poly: str | None, directory: Path, requests: int, minimum_blocks: int,
                   split_bits: int = 32, pass_cells: int | None = None, tag: str = "") -> dict:
    """Forced block mode on a real KHD1: a full matching must verify, with several blocks. With
    split_bits < 32 labels are stored as low words plus breakpoints (the path q > 2^32 takes); with
    pass_cells the field rows are built a few cells at a time, in many passes."""
    dp, digest = load_dp(path)
    blocks = directory / f"{path.stem}.blocks"
    write_blocks(blocks, [(run["a"], run["t"] * run["repeat"]) for run in dp["runs"]])
    threads = 3 if split_bits != 32 else 2
    extra = ["--poly", poly] if poly else []
    if split_bits != 32:
        extra += ["--label-split-bits", str(split_bits)]
    if pass_cells:
        extra += ["--row-bytes", str(rows_bytes(dp["p"], dp["r"], threads, split_bits, pass_cells))]
    payload = directory / f"{path.stem}.{requests}{tag}.bin"
    code, metadata = run_kernel(KERNEL, dp["p"], dp["r"], blocks, payload, "--threads", str(threads),
                                "--block-requests", str(requests), *extra)
    assert code == 0 and metadata["engine"] == "gpu-wide" and metadata["blocks"] >= minimum_blocks, metadata
    if pass_cells:
        assert metadata["passes"] >= 2, metadata
    trace = metadata["trace"]  # unmatched after: start, each block, each exchange round (dashboard burndown)
    assert [step for step, _ in trace] == list(range(len(trace))), trace
    assert len(trace) == 1 + metadata["blocks"] + metadata["rounds"] - 1 and trace[0][1] == metadata["required"], trace
    assert trace[-1][1] == metadata["required"] - metadata["matched"] == 0, trace
    assert all(a[1] >= b[1] for a, b in zip(trace, trace[1:])), "unmatched count rose"
    output = directory / f"{path.stem}.{requests}{tag}.khmatch"
    publish(output, header(dp, digest, metadata), payload)
    summary = verify(output, dp, digest)
    assert summary["verified"] and summary["status"] == "full_matching", summary
    # The payload already exists now: a second run must refuse rather than overwrite it.
    completed = subprocess.run([str(KERNEL), str(dp["p"]), str(dp["r"]), str(blocks), str(payload), *extra],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert completed.returncode == 1 and "must not exist" in completed.stderr, completed.stderr
    print(f"ok block fixture {path.name}{' (' + str(split_bits) + '-bit label words)' if split_bits != 32 else ''}"
          f"{' (' + str(pass_cells) + ' cells per pass)' if pass_cells else ''}: {metadata['blocks']} blocks, "
          f"{metadata['passes']} passes, {metadata['rounds']} rounds, round-1 residual {metadata['residual_round1']}")
    return metadata


def block_synthetic(rng: random.Random, p: int, r: int, directory: Path, index: int, one_round: bool) -> dict:
    """Random cell tables with ascending rows. With one round, every block must match exactly the
    maximum matching of its own requests against its own right window (this tests the window);
    otherwise a full matching must be valid and an impossible one must exit 4, never 0 or 2. Most
    cases hold only a few rows per pass, so leftovers cross passes and rescue passes run."""
    params = field_parameters(p, r)
    q, f, budget = params["q"], params["f"], params["budget"]
    runs, stripes, cosets = [], 0, 0
    while stripes < budget and rng.random() < 0.85:
        a = rng.randint(1, p)
        copies = rng.randint(1, 3)
        if stripes + a * copies > budget or cosets + copies + 1 > q - 1:
            break
        runs.append((a, copies))
        stripes += a * copies
        cosets += copies
    if not runs:
        runs = [(1, 1)]
    alphabet = rng.choice([q, max(2, q // 2), max(2, q // 4)])
    cells = []
    for _ in range(q // f):
        cells.extend(sorted(rng.randrange(alphabet) for _ in range(f)))
    cells.extend(rng.randrange(alphabet) for _ in range(q - len(cells)))  # unused tail of the table
    table = directory / f"bcells{index}.bin"
    table.write_bytes(b"".join(value.to_bytes(4, "little") for value in cells))
    blocks = directory / f"bblocks{index}.txt"
    write_blocks(blocks, runs)
    reqs = list(requests(runs, f))
    n = len(reqs)
    adjacency = [neighbors(cells, q, f, coset, cell) for coset, cell in reqs]
    payload = directory / f"bsynthetic{index}.bin"
    cap = max(1, n // rng.choice([2, 3, 4, 6]))
    options = ["--test-cells", str(table), "--block-requests", str(cap), "--max-residual", str(n)]
    split = 32
    if index % 3 == 1:  # narrow label words: breakpoints rebuild the labels, as for q > 2^32
        narrowest = next(bits for bits in range(1, 33) if (q - 1) >> bits <= 256)
        split = rng.randint(narrowest, max(narrowest, (q - 1).bit_length() - 1))
        options += ["--label-split-bits", str(split)]
    if one_round:
        options += ["--max-rounds", "1"]
    pass_cells = None
    if index % 4 != 3:  # a few rows per pass: as many passes as blocks, or more
        pass_cells = max(1, max(a for a, _ in runs) * f // rng.choice([3, 5, 8]))
    while True:
        rows = ["--row-bytes", str(rows_bytes(p, r, 1, split, pass_cells))] if pass_cells else []
        try:
            code, metadata = run_kernel(KERNEL, p, r, blocks, payload, *options, *rows)
            break
        except AssertionError as failure:  # the layout isn't known here: grow until a block fits
            if pass_cells is None or "exceed the row budget" not in str(failure):
                raise
            pass_cells *= 2
    assert metadata["engine"] == "gpu-wide" and metadata["required"] == n, metadata
    if one_round:
        spans = [(int(a), int(b), int(m), int(k)) for a, b, m, k in re.findall(
            r"block \d+/\d+ cells=\[(\d+),(\d+)\) requests=(\d+) matched=(\d+)", metadata["stderr"])]
        assert len(spans) == metadata["blocks"] and sum(span[2] for span in spans) == n, (spans, metadata)
        window_start = 0
        for index_block, (lo, hi, m, matched) in enumerate(spans):
            window_end = q if index_block + 1 == len(spans) else window_start + m
            members = [u for u, (coset, cell) in enumerate(reqs) if lo <= cell < hi]
            assert len(members) == m, (len(members), m)
            restricted = [[v for v in adjacency[u] if window_start <= v < window_end] for u in members]
            expected = maximum_matching(restricted)
            assert matched == expected, f"block {index_block}: kernel {matched} != oracle {expected}"
            window_start += m
        if code == 4:
            assert not payload.exists(), "an incomplete run left its payload"
        return metadata
    expected = maximum_matching(adjacency)
    if expected < n:
        assert code == 4 and metadata["incomplete"], (code, metadata)
        assert payload.exists() is False
    elif code == 0:
        bits = (f - 1).bit_length()
        data = payload.read_bytes()
        assert len(data) == (n * bits + 7) // 8, "payload length"
        choices = unpack(data, n, bits)
        assert all(k < f for k in choices), "choice out of range"
        used = {adjacency[u][k] for u, k in enumerate(choices)}
        assert len(used) == n, "right vertex used twice"
    else:
        assert code == 4 and metadata["matched"] < n and not payload.exists(), (code, metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, epilog="Example: python3 tests/check.py --run")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--seed", type=int, default=20261002)
    parser.add_argument("--cases", type=int, default=60)
    arguments = parser.parse_args()
    if not arguments.run:
        parser.print_help()
        return 0
    with tempfile.TemporaryDirectory(prefix="gpu-match-check-") as temporary:
        directory = Path(temporary)
        blocks_fixture(ROOT / "examples/7_5.khdp", None, directory, 5000, 3)
        blocks_fixture(ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1", directory, 100000, 3)
        blocks_fixture(ROOT / "examples/7_5.khdp", None, directory, 5000, 3, split_bits=9, tag=".split")
        blocks_fixture(ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1", directory, 100000, 3,
                       split_bits=13, tag=".split")
        # Rows built a block or two at a time: same layout as above, in several passes.
        blocks_fixture(ROOT / "examples/7_5.khdp", None, directory, 5000, 3, pass_cells=60, tag=".passes")
        blocks_fixture(ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1", directory, 100000, 3,
                       split_bits=11, pass_cells=500, tag=".split.passes")
        rng = random.Random(arguments.seed)
        shapes = [(3, 3), (5, 3), (3, 5), (7, 3), (3, 7), (11, 3)]   # p = 2 is refused (F a power of two)
        seen = []
        for index in range(arguments.cases):
            p, r = shapes[index % len(shapes)]
            seen.append((index % 2 == 0, block_synthetic(rng, p, r, directory, index, one_round=index % 2 == 0)))
        interior = sum(1 for oracle, m in seen if oracle and m["blocks"] >= 3)
        exchanged = sum(1 for oracle, m in seen if not oracle and m["rounds"] > 1)
        completed = sum(1 for oracle, m in seen if not oracle and not m["incomplete"] and m["rounds"] > 1)
        multipass = sum(1 for _, m in seen if m["passes"] >= 2)
        crossed = sum(1 for oracle, m in seen if not oracle and m["passes"] >= 2 and
                      any(entry["pass"] > 1 for entry in m["round_log"]))
        rescued = sum(1 for _, m in seen if m["rescue_passes"] > 0)
        assert interior > 0 and exchanged > 0, "block synthetic cases never exercised interior windows or exchange"
        assert multipass > 0 and crossed > 0 and rescued > 0, (multipass, crossed, rescued)
        print(f"ok block synthetic: {arguments.cases} graphs; {sum(1 for o, _ in seen if o)} window-oracle cases "
              f"({interior} with 3+ blocks), {exchanged} used exchange rounds ({completed} completed by them); "
              f"{multipass} ran in several passes, {crossed} exchanged after pass 1, {rescued} used rescue passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
