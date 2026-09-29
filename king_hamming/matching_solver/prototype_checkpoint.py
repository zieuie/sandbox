"""Durable portable phase images for congruence-sharded matching."""

from __future__ import annotations

from array import array
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile

from matching_solver.artifacts import request_count

MAGIC = b"KHS1"
NAME = "matching.checkpoint"
ABSENT = 0xffffffff
HEADER = struct.Struct("<4s32s5IQI")


# Keep every native owner record in explicit little-endian byte order.
def collect(workers: list, n: int, q: int, phase: int) -> list[bytes]:
    """Return checked worker exports for phase, including both ownership directions."""
    result = []
    count = len(workers)
    for index, worker in enumerate(workers):
        response = worker.call("export")
        left_count = (n + count - 1 - index) // count
        right_count = (q + count - 1 - index) // count
        if (response["phase"], response["left_count"], response["right_count"]) != (phase, left_count, right_count):
            raise ValueError("worker export phase or shard dimensions mismatch")
        binary = response["_binary"]
        if len(binary) != 8 * left_count + 4 * right_count:
            raise ValueError("worker export byte length mismatch")
        result.append(binary)
    return result


# Validate every ownership direction before making a phase durable.
def check_exports(exports: list[bytes], n: int, q: int, f: int) -> int:
    """Return cardinality after checking all left/right owner records agree."""
    right = array("I", [ABSENT]) * q
    count = len(exports)
    matched = 0
    for u in range(n):
        v, choice = struct.unpack_from("<II", exports[u % count], 8 * (u // count))
        if v == ABSENT:
            if choice != ABSENT:
                raise ValueError("unmatched left has nonempty choice")
            continue
        if v >= q or choice >= f or right[v] != ABSENT:
            raise ValueError("invalid or repeated checkpoint endpoint")
        right[v] = u
        matched += 1
    for v in range(q):
        owner = v % count
        left_count = (n + count - 1 - owner) // count
        actual = struct.unpack_from("<I", exports[owner], 8 * left_count + 4 * (v // count))[0]
        if actual != right[v]:
            raise ValueError("left and right owner exports disagree")
    return matched


# Publish one full matching state only after every owner has committed the phase.
def save(directory: Path, dp: dict, digest: bytes, polynomial: list[int],
         phase: int, matched: int, workers: list) -> Path:
    """Atomically write an exact KHS1 phase image and return its immutable path."""
    n = request_count(dp)
    q = dp["q"]
    f = dp["f"]
    exports = collect(workers, n, q, phase)
    if check_exports(exports, n, q, f) != matched:
        raise ValueError("checkpoint cardinality differs from coordinator")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"phase-{phase:020d}.khstate"
    if destination.exists():
        raise FileExistsError(f"committed phase already exists: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".phase-", dir=directory)
    temporary = Path(temporary_name)
    checksum = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output:
            def write(data: bytes) -> None:
                """Write and hash one checkpoint body component."""
                output.write(data)
                checksum.update(data)
            write(HEADER.pack(MAGIC, digest, dp["p"], dp["r"], q, n, f, phase, matched))
            write(struct.pack(f"<{len(polynomial)}I", *polynomial))
            for u in range(n):
                export = exports[u % len(exports)]
                offset = 8 * (u // len(exports))
                write(export[offset:offset + 8])
            output.write(checksum.digest())
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, destination)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


# Reject wrong graph identity, torn bytes, repeated rights and inconsistent cardinality.
def load(path: Path, dp: dict, digest: bytes, polynomial: list[int],
         worker_count: int = 2) -> tuple[int, int, list[bytes]]:
    """Return phase, matched count, and portable payloads for worker_count owners."""
    path = Path(path)
    if not 2 <= worker_count <= 256:
        raise ValueError("distributed checkpoint requires two to 256 workers")
    n = request_count(dp) if "runs" in dp else dp["count"]
    q = dp["q"]
    expected = HEADER.size + 4 * len(polynomial) + 8 * n + 32
    if path.stat().st_size != expected:
        raise ValueError("distributed phase checkpoint length mismatch")
    checksum = hashlib.sha256()
    left = [array("I") for _ in range(worker_count)]
    right = [array("I", [ABSENT]) * ((q + worker_count - 1 - index) // worker_count)
             for index in range(worker_count)]
    with path.open("rb") as stream:
        def take(count: int) -> bytes:
            """Read and hash one complete checkpoint body component."""
            data = stream.read(count)
            if len(data) != count:
                raise ValueError("truncated distributed phase checkpoint")
            checksum.update(data)
            return data
        magic, saved_digest, p, r, saved_q, saved_n, f, phase, matched = HEADER.unpack(take(HEADER.size))
        if (magic, saved_digest, p, r, saved_q, saved_n, f) != (MAGIC, digest, dp["p"], dp["r"], q, n, dp["f"]):
            raise ValueError("distributed phase checkpoint input mismatch")
        coefficients = list(struct.unpack(f"<{len(polynomial)}I", take(4 * len(polynomial))))
        if coefficients != polynomial or phase < 1 or matched > n:
            raise ValueError("distributed phase checkpoint field or cursor mismatch")
        counted = 0
        for u in range(n):
            v, choice = struct.unpack("<II", take(8))
            if v == ABSENT:
                if choice != ABSENT:
                    raise ValueError("unmatched checkpoint choice is nonempty")
            else:
                if (v >= q or choice >= f or
                        right[v % worker_count][v // worker_count] != ABSENT):
                    raise ValueError("repeated or invalid checkpoint endpoint")
                right[v % worker_count][v // worker_count] = u
                counted += 1
            left[u % worker_count].extend((v, choice))
        if counted != matched or stream.read(32) != checksum.digest() or stream.read(1):
            raise ValueError("distributed phase checkpoint checksum or cardinality mismatch")
    payloads = []
    for index in range(worker_count):
        if sys.byteorder != "little":
            left[index].byteswap()
            right[index].byteswap()
        payloads.append(left[index].tobytes() + right[index].tobytes())
    return phase, matched, payloads


# Keep checkpoint control messages independent of a potentially large DP run list.
def parameters(dp: dict) -> dict:
    """Return compact identity parameters for a validated DP artifact."""
    return {key: dp[key] for key in ("p", "r", "q", "f")} | {"count": request_count(dp)}


# Validate a transported KHS1 file against its small generic-cluster manifest.
def inspect_manifest(path: Path, manifest: dict) -> None:
    """Reject checksum, graph, polynomial, cursor or cardinality mismatches."""
    phase, matched, _ = load(path, manifest["parameters"],
                             bytes.fromhex(manifest["dp_sha256"]), manifest["polynomial"])
    if (phase, matched) != (manifest["cursor"], manifest["done"]):
        raise ValueError("distributed checkpoint coverage mismatch")
