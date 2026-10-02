"""Disposable, bounded, node-shared cache for immutable DP input packets."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import shutil
from typing import Callable

from blob_store import blob_path, fetch_blob, file_digest, valid_digest


MAX_CACHE_BYTES = 4 * 1024**3
MIN_FREE_BYTES = 8 * 1024**3


def _published(root: Path) -> list[tuple[int, int, Path, str]]:
    """List only canonical published cache objects, never partial downloads."""

    objects = []
    for shard in root.iterdir():
        if not shard.is_dir() or len(shard.name) != 2 or any(
                character not in "0123456789abcdef" for character in shard.name):
            continue
        for path in shard.iterdir():
            digest = shard.name + path.name
            if not path.is_file() or not valid_digest(digest):
                continue
            status = path.stat()
            objects.append((status.st_mtime_ns, status.st_size, path, digest))
    return objects


def evict(cache_root: Path, max_bytes: int = MAX_CACHE_BYTES,
          min_free_bytes: int = MIN_FREE_BYTES) -> int:
    """Bound published and partial bytes without removing an active download."""

    cache_root.mkdir(parents=True, exist_ok=True)
    downloads = cache_root / ".downloads"
    downloads.mkdir(exist_ok=True)
    with (cache_root / ".evict.lock").open("a+b") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        objects = sorted(_published(cache_root))
        partials = []
        for path in downloads.glob("*.part"):
            digest = path.name[:-5]
            if path.is_file() and valid_digest(digest):
                status = path.stat()
                partials.append((status.st_mtime_ns, status.st_size, path, digest))
        total = sum(size for _, size, _, _ in [*objects, *partials])
        free = shutil.disk_usage(cache_root).free
        removed = 0
        for _, size, path, digest in [*objects, *sorted(partials)]:
            if total <= max_bytes and free >= min_free_bytes:
                break
            with (downloads / f"{digest}.lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                try:
                    # Another fetch may have replaced the object since listing.
                    if path.is_file():
                        current = path.stat().st_size
                        path.unlink()
                        total -= current
                        free += current
                        removed += current
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
        return removed


def checkout(
    cache_root: Path, private_root: Path, digest: str, size: int,
    locations: list[str], local_storage_root: Path | None = None,
    local_storage_url: str | None = None,
    check: Callable[[], None] | None = None,
    max_cache_bytes: int = MAX_CACHE_BYTES,
) -> Path:
    """Pin a verified local or shared packet for this lease without copying it."""

    if not valid_digest(digest) or type(size) is not int or size <= 0:
        raise ValueError("invalid dependency identity or size")
    private_root.mkdir(parents=True, exist_ok=True)
    destination = private_root / digest
    if destination.exists():
        raise FileExistsError(destination)

    # A hard link survives storage GC after checkout, without a second disk copy.
    if local_storage_root is not None:
        try:
            os.link(blob_path(local_storage_root, digest), destination)
        except OSError as error:
            if error.errno not in {errno.ENOENT, errno.EXDEV}:
                raise
        else:
            if destination.stat().st_size == size and file_digest(destination, check) == digest:
                return destination
            destination.unlink()

    local_location = (f"{local_storage_url.rstrip('/')}/blobs/{digest}"
                      if local_storage_url else None)
    ordered = ([local_location] if local_location in locations else []) + [
        location for location in locations if location != local_location]

    # Evict before a new download if free disk is already scarce. Published
    # objects alone are candidates; fetch_blob retains resumable partials.
    evict(cache_root, max_cache_bytes)
    for _ in range(2):
        packet = fetch_blob(cache_root, digest, size, ordered, check)
        with (cache_root / ".evict.lock").open("a+b") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            try:
                os.link(packet, destination)
            except FileNotFoundError:
                continue  # Another process evicted it between fetch and checkout.
            os.utime(packet, None)
        if destination.stat().st_size == size and file_digest(destination, check) == digest:
            evict(cache_root, max_cache_bytes)
            return destination
        destination.unlink()
    raise OSError("dependency cache checkout lost its verified packet")
