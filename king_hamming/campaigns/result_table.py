#!/usr/bin/env python3
"""Print one prime-by-exponent Markdown table from all retained campaigns."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dp_solver.artifacts import decode_dp
from matching_solver.adapter import decode_input
from matching_solver.artifacts import Reader, request_count

ACTIVE = {"queued", "waiting", "running", "stopping"}
DP_PROGRAMS = {"dp", "dp_distributed"}
MATCH_PROGRAMS = {"match", "match_distributed"}


def permutation_entries(dp: dict) -> int:
    """Count cells in the conceptual rows-by-(q+1) permutation array."""
    return (dp["theta"] * dp["f"] * dp["f"] + dp["q"]) * (dp["q"] + 1)


def certificate_status(path: Path, artifact_hash: str, dp: dict, dp_hash: bytes) -> int:
    """Check the archived certificate's hashes and bounded header, not its full proof."""
    size = path.stat().st_size
    if size < 68:
        raise ValueError(f"short matching certificate: {path}")
    content_hash = hashlib.sha256()
    payload_hash = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = size - 32
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError(f"truncated matching certificate: {path}")
            content_hash.update(block)
            payload_hash.update(block)
            remaining -= len(block)
        trailer = stream.read(32)
        content_hash.update(trailer)
        if payload_hash.digest() != trailer or content_hash.hexdigest() != artifact_hash:
            raise ValueError(f"matching certificate hash mismatch: {path}")
        stream.seek(0)
        reader = Reader(stream, size)
        if reader.take(4) != b"KHM1" or reader.take(32) != dp_hash:
            raise ValueError(f"matching certificate has wrong DP dependency: {path}")
        p, r = reader.uint(), reader.uint()
        if (p, r) != (dp["p"], dp["r"]):
            raise ValueError(f"matching certificate has wrong field: {path}")
        for _ in range(r + 1):
            reader.uint()  # primitive polynomial; already verified when archived
        status, required, matched = reader.uint(), reader.uint(), reader.uint()
        if status not in (0, 1) or required != request_count(dp) or not 0 <= matched <= required:
            raise ValueError(f"matching certificate has invalid outcome: {path}")
        if (status == 0) != (matched == required):
            raise ValueError(f"matching certificate has inconsistent cardinality: {path}")
        return status


def records(deployments: Path) -> dict[tuple[int, int], dict]:
    """Merge complete results and active runs from every local leader, read-only."""
    fields: dict[tuple[int, int], dict] = {}
    databases = sorted(deployments.glob("*/leader.sqlite"))
    if not databases:
        raise ValueError(f"no retained leader.sqlite files under {deployments}")
    for database in databases:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            setting = connection.execute(
                "SELECT value FROM settings WHERE key='campaign_state'").fetchone()
            dispatch_running = setting is not None and setting[0] == "running"
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            root_clause = " WHERE parent_run_id IS NULL" if "parent_run_id" in columns else ""
            query = "SELECT run_id,specification,state,artifact_hash FROM runs" + root_clause
            for run_id, encoded, state, artifact_hash in connection.execute(query):
                specification = json.loads(encoded)
                program = specification.get("program")
                if program in DP_PROGRAMS:
                    arguments = specification["arguments"]
                    key = arguments["p"], arguments["r"]
                    record = fields.setdefault(key, {"entries": None, "outcomes": set(), "active": False})
                    if state == "complete":
                        path = database.parent / "results" / f"{key[0]}_{key[1]}_{run_id}.khdp"
                        if not path.is_file():
                            raise FileNotFoundError(f"completed DP artifact not collected: {path}")
                        raw = path.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != artifact_hash:
                            raise ValueError(f"DP artifact hash mismatch: {path}")
                        dp = decode_dp(raw)
                        if (dp["p"], dp["r"]) != key:
                            raise ValueError(f"DP artifact has wrong field: {path}")
                        count = permutation_entries(dp)
                        if record["entries"] is not None and record["entries"] != count:
                            raise ValueError(f"conflicting DP results for {key}")
                        record["entries"] = count
                    elif state in ACTIVE and dispatch_running:
                        record["active"] = True
                elif program in MATCH_PROGRAMS:
                    dp, _, digest = decode_input(specification)
                    key = dp["p"], dp["r"]
                    record = fields.setdefault(key, {"entries": None, "outcomes": set(), "active": False})
                    count = permutation_entries(dp)
                    if record["entries"] is not None and record["entries"] != count:
                        raise ValueError(f"conflicting DP results for {key}")
                    record["entries"] = count
                    if state == "complete":
                        filename = f"{key[0]}_{key[1]}_{run_id}.khmatch"
                        path = database.parent / "matching-results" / filename
                        if not path.is_file():
                            path = database.parent / "results" / filename
                        if not path.is_file():
                            raise FileNotFoundError(f"completed matching artifact not collected: {path}")
                        record["outcomes"].add(certificate_status(path, artifact_hash, dp, digest))
                    elif state in ACTIVE and dispatch_running:
                        record["active"] = True
        finally:
            connection.close()
    return fields


def markdown(fields: dict[tuple[int, int], dict]) -> str:
    """Render exact entry counts, matching outcomes, and current activity."""
    primes = sorted({p for p, _ in fields})
    exponents = sorted({r for _, r in fields})
    header = "| Prime \\ Exponent | " + " | ".join(map(str, exponents)) + " |"
    lines = [
        "# Results across retained campaigns",
        "",
        "Each number is the exact number of entries in the permutation array. "
        "`^` means a completed full matching; `*` means a certified Hall obstruction "
        "for a tested polynomial (not necessarily every polynomial). "
        "`(running)` means DP or matching is in progress; `—` means no completed DP value.",
        "",
        header,
        "| --- | " + " | ".join("---:" for _ in exponents) + " |",
    ]
    for p in primes:
        cells = []
        for r in exponents:
            record = fields.get((p, r))
            if record is None:
                cells.append("—")
                continue
            count = record["entries"]
            if count is None:
                cells.append("(running)" if record["active"] else "—")
                continue
            symbol = "^" if 0 in record["outcomes"] else "*" if 1 in record["outcomes"] else ""
            running = " (running)" if record["active"] and not symbol else ""
            cells.append(f"{count:,}{symbol}{running}")
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployments", type=Path,
                        default=ROOT / "cluster" / "deployments",
                        help="directory containing retained campaign state directories")
    arguments = parser.parse_args()
    print(markdown(records(arguments.deployments)))


if __name__ == "__main__":
    main()
