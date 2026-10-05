"""RAM scratch for a tile's large temporary files (its halo and the kernel's output).

Each 4096-side tile wrote about 350 MB to the work disk and deleted it seconds later:
about 0.5 TB a day on a P600 worker and 4.6 TB a day on merlin, whose SSD was gaining
about 1% of its rated wear per day. Those files never need to survive the process, so
they go to a tmpfs (by default /dev/shm, which every host already has) when it has room.
Only the tile's packet, which the agent publishes, stays on disk.

Claims are counted under a lock so concurrent tiles cannot overfill the tmpfs together:
a claim fits when every live claim plus it stays within the limit (a quarter of RAM by
default, well inside the per-tile memory the leader already reserves) and the tmpfs has
the room. A tile that does not fit uses the disk, exactly as before. A claim whose
process has died (killed, so its cleanup never ran) is swept by the next claim.

KH_TILE_SCRATCH: the tmpfs directory, or "disk" to always use the work disk.
KH_TILE_SCRATCH_MAX_BYTES: the limit on all live claims together.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import uuid

DEFAULT_ROOT = Path("/dev/shm")
PREFIX = "kh-tile-"
LOCK_NAME = ".kh-tile-scratch.lock"
CLAIM_NAME = "claim.json"
HEADROOM_BYTES = 256 * 1024**2  # left free on the tmpfs for everything else


def root() -> Path | None:
    """Return the configured scratch root, or None to use the work disk."""

    configured = os.environ.get("KH_TILE_SCRATCH")
    if configured == "disk":
        return None
    path = Path(configured) if configured else DEFAULT_ROOT
    return path if path.is_dir() and os.access(path, os.W_OK | os.X_OK) else None


def limit() -> int:
    """Return the byte limit on all live claims together."""

    configured = os.environ.get("KH_TILE_SCRATCH_MAX_BYTES")
    if configured:
        return max(0, int(configured))
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 4


def process_start(pid: int) -> str | None:
    """Return pid's start time in clock ticks (to tell it from a reused pid), or None if it is gone."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[19]


def live_claims(base: Path) -> int:
    """Sum the live claims under base and delete the dead ones; caller holds the lock."""

    total = 0
    for directory in base.glob(PREFIX + "*"):
        try:
            claim = json.loads((directory / CLAIM_NAME).read_text())
            alive = process_start(int(claim["pid"])) == claim["start"]
        except (OSError, ValueError, KeyError, TypeError):
            alive = False  # unreadable: a claim interrupted while being made, or not ours
        if alive:
            total += int(claim["bytes"])
        else:
            shutil.rmtree(directory, ignore_errors=True)
    return total


def claim(need: int) -> Path | None:
    """Return a private tmpfs directory with room for need bytes, or None to use the work disk."""

    base = root()
    if base is None:
        return None
    with open(base / LOCK_NAME, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        claimed = live_claims(base)
        stats = os.statvfs(base)
        if claimed + need > limit() or need + HEADROOM_BYTES > stats.f_bavail * stats.f_frsize:
            return None
        directory = base / f"{PREFIX}{os.getpid()}-{uuid.uuid4().hex[:8]}"
        directory.mkdir(mode=0o700)
        (directory / CLAIM_NAME).write_text(json.dumps(
            {"pid": os.getpid(), "start": process_start(os.getpid()), "bytes": need}))
        return directory


def release(directory: Path | None) -> None:
    """Delete a claimed directory and everything in it."""

    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
