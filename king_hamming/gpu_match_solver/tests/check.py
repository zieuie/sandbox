#!/usr/bin/env python3
"""Check kh_gpu_match_kernel against the KHM1 verifier and a brute-force matching oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from matching_solver.artifacts import header, load_dp, publish, verify  # noqa: E402

KERNEL = HERE / "kh_gpu_match_kernel"
CPU_KERNEL = ROOT / "matching_solver" / "kh_match_kernel"


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
    if completed.returncode not in (0, 2) or metadata is None:
        raise AssertionError(f"{kernel.name} failed ({completed.returncode}): {completed.stderr[-2000:]}")
    return completed.returncode, metadata


def fixture(path: Path, poly: str | None, directory: Path) -> None:
    """Full pipeline on a real KHD1: GPU payload -> KHM1 -> independent verifier; auto poly agrees with CPU."""
    dp, digest = load_dp(path)
    blocks = directory / f"{path.stem}.blocks"
    write_blocks(blocks, [(run["a"], run["t"] * run["repeat"]) for run in dp["runs"]])
    extra = ["--poly", poly] if poly else []
    payload = directory / f"{path.stem}.gpu.bin"
    code, metadata = run_kernel(KERNEL, dp["p"], dp["r"], blocks, payload, "--threads", "2", *extra)
    assert code == 0 and metadata["matched"] == metadata["required"], metadata
    trace = metadata["trace"]  # unmatched after: start, greedy, each augmenting phase (dashboard burndown)
    assert [step for step, _ in trace] == list(range(len(trace))) and len(trace) == metadata["phases"] + 2, trace
    assert trace[0][1] == metadata["required"] and trace[-1][1] == 0, trace
    assert all(a[1] >= b[1] for a, b in zip(trace, trace[1:])), "unmatched count rose"
    output = directory / f"{path.stem}.khmatch"
    publish(output, header(dp, digest, metadata), payload)
    summary = verify(output, dp, digest)
    assert summary["verified"] and summary["status"] == "full_matching", summary
    if not poly and CPU_KERNEL.exists():
        _, cpu = run_kernel(CPU_KERNEL, dp["p"], dp["r"], blocks, directory / f"{path.stem}.cpu.bin")
        assert cpu["polynomial"] == metadata["polynomial"] and cpu["candidate"] == metadata["candidate"], (cpu, metadata)
    print(f"ok fixture {path.name}: {metadata['matched']} requests, {metadata['phases']} phases")


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


def synthetic(rng: random.Random, p: int, r: int, directory: Path, index: int) -> None:
    """Random (often deficient) cell tables: cardinality vs oracle, edges and Hall witness checked."""
    params = field_parameters(p, r)
    q, f, budget = params["q"], params["f"], params["budget"]
    runs, stripes, cosets = [], 0, 0
    while stripes < budget and rng.random() < 0.8:
        a = rng.randint(1, p)
        copies = rng.randint(1, 3)
        if stripes + a * copies > budget or cosets + copies + 1 > q - 1:
            break
        runs.append((a, copies))
        stripes += a * copies
        cosets += copies
    if not runs:
        runs = [(1, 1)]
    alphabet = rng.choice([q, max(2, q // 4), max(2, f)])
    cells = [rng.randrange(alphabet) for _ in range(q)]
    table = directory / f"cells{index}.bin"
    table.write_bytes(b"".join(value.to_bytes(4, "little") for value in cells))
    blocks = directory / f"blocks{index}.txt"
    write_blocks(blocks, runs)
    payload = directory / f"synthetic{index}.bin"
    code, metadata = run_kernel(KERNEL, p, r, blocks, payload, "--test-cells", str(table))
    adjacency = [neighbors(cells, q, f, coset, cell) for coset, cell in requests(runs, f)]
    n = len(adjacency)
    expected = maximum_matching(adjacency)
    assert metadata["required"] == n and metadata["matched"] == expected, (metadata, expected)
    obstructed = expected < n
    assert code == (2 if obstructed else 0)
    bits = (f - 1 + obstructed).bit_length()
    data = payload.read_bytes()
    choices = unpack(data, n, bits)
    used = set()
    for u, value in enumerate(choices):
        if obstructed and value == 0:
            continue
        v = adjacency[u][value - obstructed]
        assert v not in used, "right vertex used twice"
        used.add(v)
    assert len(used) == expected
    if obstructed:
        hall_offset = (n * bits + 7) // 8
        members = unpack(data[hall_offset:], n, 1)
        hall = [u for u in range(n) if members[u]]
        neighborhood = {v for u in hall for v in adjacency[u]}
        assert len(neighborhood) < len(hall), "Hall witness is not deficient"
        assert len(hall) - len(neighborhood) == n - expected, "Hall witness does not certify maximality"
        assert metadata["hall_left"] == len(hall) and metadata["hall_right"] == len(neighborhood)
    return obstructed


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
        fixture(ROOT / "examples/3_3.khdp", None, directory)
        fixture(ROOT / "examples/5_3.khdp", "2,3,0,1", directory)
        fixture(ROOT / "examples/7_5.khdp", None, directory)
        fixture(ROOT / "matching_solver/examples/13_5.khdp", "2,4,0,0,0,1", directory)
        rng = random.Random(arguments.seed)
        shapes = [(2, 3), (2, 5), (3, 3), (5, 3), (2, 7), (3, 5)]
        obstructions = 0
        for index in range(arguments.cases):
            p, r = shapes[index % len(shapes)]
            obstructions += bool(synthetic(rng, p, r, directory, index))
        assert obstructions > 0, "synthetic cases never exercised a Hall obstruction"
        print(f"ok synthetic: {arguments.cases} graphs, {obstructions} certified obstructions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
