"""Streaming validation of portable owned phase images (KMP1)."""
from __future__ import annotations
import struct
import json
import subprocess
from pathlib import Path

from matching_solver.artifacts import request_count

MAGIC = 0x314e574f504d484b
HEADER = struct.Struct("<10Q32s")
ROW = struct.Struct("<4I")
NONE = 2**32 - 1


def owned_count(dp, workers, rank):
    return sum(max(0, (run["a"] * dp["f"] - 1 - rank) // workers + 1) *
               run["t"] * run["repeat"] for run in dp["runs"])


def name(rank):
    return f"owner-{rank}.kmp"


def size(dp, workers, rank):
    return HEADER.size + 4 * (dp["r"] + 1) + ROW.size * owned_count(dp, workers, rank)


def inspect_header(stream, dp, digest, polynomial, workers, rank):
    raw = stream.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise ValueError("truncated owned image header")
    magic, p, r, q, n, w, owner, phase, matched, count, identity = HEADER.unpack(raw)
    if (magic, p, r, q, n, w, owner, count, identity) != (
            MAGIC, dp["p"], dp["r"], dp["q"], request_count(dp), workers, rank,
            owned_count(dp, workers, rank), digest):
        raise ValueError("owned image input/ownership mismatch")
    if matched > n or phase > n:
        raise ValueError("owned image progress outside graph")
    if stream.read(4 * len(polynomial)) != struct.pack(f"<{len(polynomial)}I", *polynomial):
        raise ValueError("owned image polynomial mismatch")
    return phase, matched


def owned_ids(dp, workers, rank):
    first = 0
    for run in dp["runs"]:
        width = run["a"] * dp["f"]
        for _ in range(run["t"] * run["repeat"]):
            for cell in range(rank, width, workers):
                yield first + cell
            first += width


def validate_python(paths, dp, digest, polynomial, workers, expected_phase=None, expected_done=None):
    """Validate shape, identity, coverage, cardinality and unique right endpoints.

    Only q/8 auxiliary bytes are allocated. Native restore additionally checks
    each saved edge against the reconstructed field before resuming any search.
    CAS hashes provide transport-integrity validation in the generic runtime.
    """
    seen = bytearray((dp["q"] + 7) // 8)
    common = None
    total = 0
    for rank in range(workers):
        path = Path(paths[name(rank)])
        if path.stat().st_size != size(dp, workers, rank):
            raise ValueError("owned image size mismatch")
        with path.open("rb") as stream:
            progress = inspect_header(stream, dp, digest, polynomial, workers, rank)
            if common is not None and progress != common:
                raise ValueError("mixed owned image phases")
            common = progress
            for uid in owned_ids(dp, workers, rank):
                raw = stream.read(ROW.size)
                if len(raw) != ROW.size:
                    raise ValueError("truncated owned image record")
                actual, right, choice, reserved = ROW.unpack(raw)
                if actual != uid or reserved:
                    raise ValueError("owned image request coverage mismatch")
                if right == NONE:
                    if choice != NONE:
                        raise ValueError("unmatched owned image choice")
                    continue
                if right >= dp["q"] or choice >= dp["f"]:
                    raise ValueError("owned image assignment bounds")
                byte, mask = right // 8, 1 << (right % 8)
                if seen[byte] & mask:
                    raise ValueError("owned image repeats right endpoint")
                seen[byte] |= mask
                total += 1
            if stream.read(1):
                raise ValueError("owned image trailing bytes")
    if common is None or total != common[1]:
        raise ValueError("owned image cardinality mismatch")
    if expected_phase is not None and common[0] != expected_phase:
        raise ValueError("owned image cursor mismatch")
    if expected_done is not None and common[1] != expected_done:
        raise ValueError("owned image committed count mismatch")
    return common


def validate(paths, dp, digest, polynomial, workers, expected_phase=None, expected_done=None):
    """Use the bounded C checker; retain the Python implementation as a test oracle."""
    metadata = "\n".join([
        f"{dp['p']} {dp['r']} {dp['q']} {dp['f']} {dp['budget']} {request_count(dp)} {workers} "
        f"{expected_phase if expected_phase is not None else -1} {expected_done if expected_done is not None else -1} "
        f"{digest.hex()} {len(dp['runs'])}",
        " ".join(map(str, polynomial)),
        *(f"{run['a']} {run['t'] * run['repeat']}" for run in dp["runs"]),
    ]) + "\n"
    result = subprocess.run([str(Path(__file__).with_name("kh_check_images")),
                             *(str(paths[name(i)]) for i in range(workers))],
                            input=metadata, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"invalid owned checkpoint: {result.stderr.strip()}")
    summary = json.loads(result.stdout)
    return summary["phase"], summary["done"]
