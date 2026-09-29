"""Portable manifest validation for a committed native KHC1 matching phase."""

from __future__ import annotations

import struct
import hashlib
from pathlib import Path

from matching_solver.artifacts import request_count

NAME = "matching.checkpoint"
LAYOUT = "KHC1"
FNV_START = 14695981039346656037
FNV_PRIME = 1099511628211
MASK = (1 << 64) - 1


# Identify the exact native checkpoint shape from immutable input dimensions.
def size(dp: dict) -> int:
    """Return the expected byte length for validated DP document dp."""
    blocks = len(dp["runs"]) if "runs" in dp else dp["blocks"]
    count = request_count(dp) if "runs" in dp else dp["count"]
    return 88 + 4 * (dp["r"] + 1) + 20 * blocks + 8 * count


# Check the complete stream without allocating the matching-size payload.
def inspect(path: Path, dp: dict, digest: bytes, polynomial: list[int]) -> dict:
    """Return phase/coverage metadata after validating path identity, blocks and checksum."""
    path = Path(path)
    expected_count = request_count(dp) if "runs" in dp else dp["count"]
    expected_blocks = len(dp["runs"]) if "runs" in dp else dp["blocks"]
    if path.stat().st_size != size(dp):
        raise ValueError("matching checkpoint length mismatch")
    checksum = FNV_START
    with path.open("rb") as stream:
        def take(length: int) -> bytes:
            """Read and hash exactly length checkpoint bytes."""
            nonlocal checksum
            data = stream.read(length)
            if len(data) != length:
                raise ValueError("truncated matching checkpoint")
            for byte in data:
                checksum = ((checksum ^ byte) * FNV_PRIME) & MASK
            return data

        if take(4) != b"KHC1" or take(32) != digest:
            raise ValueError("matching checkpoint input identity mismatch")
        p, r, q, f, count, blocks, matched = struct.unpack("<7I", take(28))
        phase, scans = struct.unpack("<2Q", take(16))
        if (p, r, q, f, count, blocks) != (dp["p"], dp["r"], dp["q"], dp["f"], expected_count, expected_blocks):
            raise ValueError("matching checkpoint graph dimensions mismatch")
        if phase < 1 or matched > count:
            raise ValueError("invalid matching checkpoint coverage")
        coefficients = list(struct.unpack(f"<{r + 1}I", take(4 * (r + 1))))
        if coefficients != polynomial:
            raise ValueError("matching checkpoint polynomial mismatch")
        block_hash = hashlib.sha256()
        first = coset = 0
        for index in range(expected_blocks):
            encoded = take(20)
            block_hash.update(encoded)
            stored_first, stored_coset, copies, stripes = struct.unpack("<Q3I", encoded)
            if stored_first != first or stored_coset != coset or copies < 1 or stripes < 1 or stripes > p:
                raise ValueError("matching checkpoint request block structure mismatch")
            if "runs" in dp:
                run = dp["runs"][index]
                if (copies, stripes) != (run["t"] * run["repeat"], run["a"]):
                    raise ValueError("matching checkpoint request blocks mismatch")
            first += stripes * copies * f
            coset += copies
        if first != count:
            raise ValueError("matching checkpoint request count mismatch")
        if "blocks_sha256" in dp and block_hash.hexdigest() != dp["blocks_sha256"]:
            raise ValueError("matching checkpoint request block digest mismatch")
        counted = 0
        for _ in range(count):
            right, choice = struct.unpack("<2I", take(8))
            if right == 0xffffffff:
                if choice != 0xffffffff:
                    raise ValueError("unmatched checkpoint choice is not empty")
            elif right >= q or choice >= f:
                raise ValueError("invalid checkpoint assignment range")
            else:
                counted += 1
        if counted != matched:
            raise ValueError("matching checkpoint cardinality mismatch")
        if struct.unpack("<Q", stream.read(8))[0] != checksum or stream.read(1):
            raise ValueError("matching checkpoint checksum mismatch")
    return {"cursor": phase, "done": matched, "total": count, "scans": scans}


# Recheck transported bytes against the signed manifest's immutable graph identity.
def inspect_manifest(path: Path, manifest: dict) -> None:
    """Reject any native file whose cursor, coverage, or graph differs from manifest."""
    digest = bytes.fromhex(manifest["dp_sha256"])
    metadata = inspect(path, manifest["parameters"], digest, manifest["polynomial"])
    if any(metadata[key] != manifest[key] for key in ("done", "total")) or metadata["cursor"] != manifest["cursor"]:
        raise ValueError("matching checkpoint native coverage mismatch")


# The manifest carries a compact cryptographic graph-block fingerprint.
def block_digest(dp: dict) -> str:
    """Return SHA-256 of canonical native request blocks for validated DP dp."""
    digest = hashlib.sha256()
    first = coset = 0
    for run in dp["runs"]:
        copies = run["t"] * run["repeat"]
        stripes = run["a"]
        digest.update(struct.pack("<Q3I", first, coset, copies, stripes))
        first += stripes * copies * dp["f"]
        coset += copies
    return digest.hexdigest()


# Keep a potentially large DP run list out of checkpoint control messages.
def parameters(dp: dict) -> dict:
    """Return compact graph identity for the matching checkpoint manifest."""
    return {key: dp[key] for key in ("p", "r", "q", "f")} | {
        "count": request_count(dp), "blocks": len(dp["runs"]),
        "blocks_sha256": block_digest(dp),
    }
