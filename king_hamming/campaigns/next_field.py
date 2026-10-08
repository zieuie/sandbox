#!/usr/bin/env python3
"""Start the quickest field not yet calculated, when the cluster has room (Zooey, 2026-10-07:
"if there's room to start a new field, please start the next smallest (meaning quickest) field
that hasn't been calculated").

Run at each hourly check. Without --submit it only reports. Every field counts, whatever its p or
r (Zooey: "regardless of the size of p or r or any other constraint which the feeder was
previously hung up on"), as long as the DP solver can compute it at all (p^3 and q within 64-bit
widths) and its tiles fit the workers' disks. Fields no matcher can take yet (p = 2 past F =
65,534, q past 2^40, a certificate larger than the matching host's disk) still get their DP
value, a table entry without the ^, and are ranked by DP time alone. A field already submitted (the
manifest, or any root in the leader; Zooey's paused 3^21 among them) is never started again. There
is room when no DP root is active (queued, waiting or running) apart from paused ones.

Fields are ranked by estimated hours, DP plus matching, from a model fitted on 2026-10-03..07
fields (GPU era, 9 DP GPUs):
- compute: raw visits / (5.8e13 per GPU-hour), from 31^7 (2.54e16 visits, 438 GPU-hours);
- tile overhead: 0.0005 h + 0.0058 h per unit of halo/side, from 5^15 (36,481 tiles in 1.9 h)
  and the deep halos of 37^5 (side 512, 19 h) and 107^3..113^3 (side 512, 6-9 h);
- the critical path: 2 * tiles-per-side - 1 tiles one after another, each taking about
  20 s + 10 s * (side / 2048)^2 of fetching and publishing plus its share of compute, times 0.8
  for overlap (light fields are latency-bound: 2^31 took 0.95 h on almost no arithmetic);
- matching: about an hour per 2e10 requests on the block matcher (31^7, 5^15), at least 10
  minutes; fields only the wide matcher takes (F above 65,534 or q from 2^36) about 5.5e9 an hour
  plus verification, from 7^13 (9.7e10 requests: 17.5 h, then about 4 h of checks on its drive).
It reproduces those fields within about 20%; it is a ranking, not a promise. The tile side is
the one with the lowest estimate (bigger tiles for light fields, CAMPAIGN_NOTES item 26).

Example: python3 campaigns/next_field.py --state cluster/deployments/continuous-campaign [--submit]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dp_solver import scheduling  # noqa: E402

GPUS = 9                       # DP GPUs (merlin is kept for matching)
VISITS_PER_GPU_HOUR = 5.8e13
TILE_BASE_HOURS = 0.0005
TILE_HALO_HOURS = 0.0058       # per unit of halo rows / tile side
MATCH_REQUESTS_PER_HOUR = 2e10
WIDE_REQUESTS_PER_HOUR = 5.5e9
WIDE_VERIFY_HOURS_PER_REQUEST = 4 / 9.7e10
MIN_MATCH_HOURS = 1 / 6
MIN_TILES = 16                 # enough tiles to spread over the fleet
TILE_LATENCY_SECONDS = 20.0    # fetch, launch and publish of one tile at side 2048 ...
TILE_AREA_SECONDS = 10.0       # ... plus this much per (side / 2048)^2
CRITICAL_OVERLAP = 0.8
MAX_PRIME, MAX_EXPONENT = 1621, 63   # the DP's own limits (p^3 and q in 64 bits)
BLOCK_MAX_Q, WIDE_MAX_Q, BLOCK_MAX_F = 2**36 - 1, 2**40 - 1, 65534
ACTIVE = ("queued", "waiting", "running", "stopping")


def primes(limit: int) -> list[int]:
    sieve = bytearray([1]) * (limit + 1)
    sieve[:2] = b"\0\0"
    for value in range(2, int(limit ** 0.5) + 1):
        if sieve[value]:
            sieve[value * value::value] = bytearray(len(sieve[value * value::value]))
    return [value for value, flag in enumerate(sieve) if flag]


def dp_hours(p: int, r: int, side: int, visits: float) -> float:
    """Estimated DP hours with tiles of this side (see the module docstring)."""
    budget = scheduling.dp_estimate({"arguments": {"p": p, "r": r}})["budget"]
    per_side = -(-budget // side)
    tiles = per_side * per_side
    area = max(1.0, (side / 2048) ** 2)
    halo = TILE_HALO_HOURS * (p * p) / side
    compute = visits / VISITS_PER_GPU_HOUR
    throughput = (compute + tiles * (TILE_BASE_HOURS * area + halo)) / GPUS
    latency = (TILE_LATENCY_SECONDS + TILE_AREA_SECONDS * (side / 2048) ** 2) / 3600
    critical = (2 * per_side - 1) * (latency + halo + compute / tiles) * CRITICAL_OVERLAP
    return max(throughput, critical)


def certificate_bytes(q: int, f: int) -> int:
    return q * (f - 1).bit_length() // 8 + 4096


def matchable(p: int, r: int, q: int, f: int, disk_bytes: int) -> str | None:
    """None if a matcher can take the field, else why not."""
    if p == 2 and q > BLOCK_MAX_Q:
        return "p = 2 needs the block matcher (q below 2^36)"
    if q > WIDE_MAX_Q:
        return "q above the wide matcher's 2^40"
    if f > BLOCK_MAX_F and p == 2:
        return "F too large for the block matcher"
    if certificate_bytes(q, f) > disk_bytes:
        return f"certificate ({certificate_bytes(q, f) / 2**30:.0f} GiB) larger than any free disk"
    return None


def candidates(known: set[tuple[int, int]], settings: dict, disk_bytes: int,
               worker_disk_bytes: int | None = None) -> list[dict]:
    """Every field not yet submitted that the DP can compute, quickest first. Fields no matcher can
    take yet are included with their reason in "dp_only" and no matching time."""
    found = []
    max_tile_bytes = int(settings.get("max_tile_bytes", 2 * 1024**3))
    max_tiles = int(settings.get("max_tiles", scheduling.DEFAULT_MAX_TILES))
    threads = int(settings.get("dp_threads", 16))
    for p in primes(MAX_PRIME):
        for r in range(3, MAX_EXPONENT + 1, 2):
            if (p, r) in known:
                continue
            try:
                estimate = scheduling.dp_estimate({"arguments": {"p": p, "r": r}})
            except ValueError:
                break   # larger r only grows
            q, f = estimate["q"], p ** (r // 2)
            if estimate["raw_visits"] / VISITS_PER_GPU_HOUR / GPUS > 24 * 30:
                break   # over a month of DP: not a candidate, nor anything larger in this row
            dp_only = matchable(p, r, q, f, disk_bytes)
            best = None
            for side in scheduling.TILE_SIDES:
                plan = scheduling.plan_tiles(p, r, threads, max_tile_bytes, max_tiles, sides=(side,))
                if plan is None or (plan["tiles"] < MIN_TILES and side != scheduling.TILE_SIDES[0]):
                    continue
                stored = 3 * scheduling.stored_bytes(p, r, side, int(settings.get("tile_format", 2)))
                if worker_disk_bytes is not None and stored > worker_disk_bytes:
                    continue   # three copies of its tiles must fit the workers' disks
                hours = dp_hours(p, r, side, estimate["raw_visits"])
                if best is None or hours < best["dp_hours"]:
                    best = {"p": p, "r": r, "q": q, "side": side, "tiles": plan["tiles"],
                            "reserve": plan["reserve"], "dp_hours": hours,
                            "state_bytes": estimate["state_bytes"], "visits": estimate["raw_visits"]}
            if best is None:
                continue
            wide = f > BLOCK_MAX_F or q > BLOCK_MAX_Q
            best["dp_only"] = dp_only
            best["match_hours"] = 0.0 if dp_only else max(
                MIN_MATCH_HOURS, q / WIDE_REQUESTS_PER_HOUR + q * WIDE_VERIFY_HOURS_PER_REQUEST
                if wide else q / MATCH_REQUESTS_PER_HOUR)
            best["hours"] = best["dp_hours"] + best["match_hours"]
            found.append(best)
    return sorted(found, key=lambda item: (item["hours"], item["q"]))


def specification(item: dict, settings: dict) -> dict:
    return {"program": "dp_distributed", "arguments": {
        "p": item["p"], "r": item["r"], "threads": int(settings.get("dp_threads", 16)),
        "tile_side": item["side"], "max_state_bytes": item["state_bytes"],
        "max_visits": max(10**15, int(item["visits"])), "max_tile_bytes": item["reserve"],
        "artifact_format": "KHD1", "max_tiles": int(settings.get("max_tiles", scheduling.DEFAULT_MAX_TILES)),
        "tile_format": int(settings.get("tile_format", 2))}}


def worker_disk(connection: sqlite3.Connection) -> int:
    """Half the free disk of the DP workers (machines other than the largest-GPU host): room for
    three copies of a field's tiles alongside everything else."""
    rows = connection.execute("SELECT gpus_json,storage_free_bytes FROM nodes WHERE gpus_json NOT IN ('','[]') "
                              "AND last_heartbeat > strftime('%s','now') - 600").fetchall()
    sizes = [max([int(g.get("total_bytes", 0)) for g in json.loads(gpus)] or [0]) for gpus, _ in rows]
    return sum(max(0, int(free or 0)) for (_, free), size in zip(rows, sizes) if size != max(sizes)) // 2


def room(connection: sqlite3.Connection) -> tuple[bool, str]:
    """Room for a new field: no DP root active (paused ones don't count)."""
    active = connection.execute(
        "SELECT json_extract(specification,'$.arguments.p'),json_extract(specification,'$.arguments.r') "
        "FROM runs WHERE parent_run_id IS NULL AND json_extract(specification,'$.program')='dp_distributed' "
        f"AND state IN ({','.join('?' * len(ACTIVE))})", ACTIVE).fetchall()
    if active:
        return False, "DP running: " + ", ".join(f"{p}^{r}" for p, r in active)
    return True, "no DP running"


def largest_free_disk(connection: sqlite3.Connection) -> int:
    """The most a certificate could use: the best disk of the hosts with the largest GPU, where
    large matchings run (gpu_policy.wide_plan: merlin, its system disk or /mnt/khdata)."""
    rows = connection.execute("SELECT gpus_json,storage_free_bytes,large_free_bytes FROM nodes "
                              "WHERE gpus_json NOT IN ('','[]')").fetchall()
    sizes = [max([int(g.get("total_bytes", 0)) for g in json.loads(gpus)] or [0]) for gpus, _, _ in rows]
    if not sizes:
        return 0
    return max(max(int(a or 0), int(b or 0)) for (_, a, b), size in zip(rows, sizes) if size == max(sizes))


PENDING = "next_field_pending.json"   # submitted roots not yet in the manifest (the feeder held its lock)


def record_pending(state: Path) -> int:
    """Move submitted roots into the feeder's manifest if its lock is free now; return how many
    remain pending. The feeder holds the lock for a whole pass, which can be hours (7^13's archive
    check), so a new field is enqueued at once and recorded for the feeder here, later if need be."""
    path = state / PENDING
    pending = json.loads(path.read_text()) if path.exists() else []
    if not pending:
        return 0
    with (state / "pipeline.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return len(pending)
        manifest = json.loads((state / "manifest.json").read_text())
        present = {entry.get("run_id") for entry in manifest["entries"]}
        manifest["entries"].extend(entry for entry in pending if entry.get("run_id") not in present)
        temporary = state / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, indent=2) + "\n")
        temporary.replace(state / "manifest.json")
        path.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=ROOT / "cluster/deployments/continuous-campaign")
    parser.add_argument("--submit", action="store_true", help="start the quickest field if there is room")
    parser.add_argument("--show", type=int, default=8, help="candidates to list")
    arguments = parser.parse_args()
    state = arguments.state
    waiting = record_pending(state)
    if waiting:
        print(f"{waiting} submitted field(s) wait for the feeder's lock to enter its manifest")
    pipeline = json.loads((state / "pipeline.json").read_text())
    settings = pipeline["settings"]
    connection = sqlite3.connect(f"file:{state / 'leader.sqlite'}?mode=ro", uri=True)
    manifest = json.loads((state / "manifest.json").read_text())
    pending = json.loads((state / PENDING).read_text()) if (state / PENDING).exists() else []
    known = {(e["specification"]["arguments"]["p"], e["specification"]["arguments"]["r"])
             for e in manifest["entries"] + pending if e["specification"].get("program") == "dp_distributed"}
    known |= {tuple(int(x) for x in name.split("^")) for name in pipeline["fields"]}
    known |= {(int(p), int(r)) for p, r in connection.execute(   # any root the leader has, in any state
        "SELECT json_extract(specification,'$.arguments.p'),json_extract(specification,'$.arguments.r') "
        "FROM runs WHERE parent_run_id IS NULL AND json_extract(specification,'$.program')='dp_distributed'")}
    found = candidates(known, settings, largest_free_disk(connection), worker_disk(connection))
    for item in found[:arguments.show]:
        matching = (f"DP only: {item['dp_only']}" if item["dp_only"] else f"matching {item['match_hours']:.1f} h")
        print(f"{item['p']}^{item['r']}: about {item['hours']:.1f} h (DP {item['dp_hours']:.1f} h on "
              f"{item['tiles']} tiles of {item['side']}, {matching}), q = {item['q']:.3g}")
    ok, why = room(connection)
    print(f"room: {'yes' if ok else 'no'} ({why})")
    if not (arguments.submit and ok and found):
        return 0
    item = found[0]
    spec = specification(item, settings)
    request = urllib.request.Request(manifest["leader"].rstrip("/") + "/v1/enqueue",
                                     data=json.dumps({"specification": spec}).encode(),
                                     headers={"Content-Type": "application/json"})
    result = json.load(urllib.request.urlopen(request, timeout=60))
    pending.append({"specification": spec, **result})
    temporary = state / (PENDING + ".tmp")
    temporary.write_text(json.dumps(pending, indent=2) + "\n")
    temporary.replace(state / PENDING)
    left = record_pending(state)
    print(f"submitted {item['p']}^{item['r']}: {result}" + (" (manifest entry pending the feeder's lock)" if left else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
