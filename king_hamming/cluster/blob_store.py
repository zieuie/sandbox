"""Durable bounded-memory blob storage and resumable peer downloads."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
import hashlib
import http.client
import os
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BLOCK_BYTES = 1024 * 1024
SYNC_BYTES = 256 * 1024 * 1024  # large blobs write through; see matching_solver.artifacts.SYNC_BYTES


# Reject names that could escape the content-addressed storage tree.
def valid_digest(digest: str) -> bool:
    """Return whether digest is a canonical SHA-256 hexadecimal string."""

    return isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


# Persist directory entries after publishing or renaming durable files.
def sync_directory(path: Path) -> None:
    """Fsync the directory path; return no value and propagate I/O failures."""

    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)

    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# Resolve a checked content hash into its local immutable filename.
def blob_path(root: Path, digest: str) -> Path:
    """Return the path under root for digest, raising for malformed hashes."""

    if not valid_digest(digest):
        raise ValueError("invalid blob digest")

    return root / digest[:2] / digest[2:]


# Hash in bounded buffers while allowing the owner to detect revoked leases.
def file_digest(path: Path, check: Callable[[], None] | None = None) -> str:
    """Return path's SHA-256, calling optional check between reads."""

    digest = hashlib.sha256()

    with path.open("rb") as source:
        while True:
            if check is not None:
                check()

            block = source.read(BLOCK_BYTES)

            if not block:
                break

            digest.update(block)

    return digest.hexdigest()


# Copy an immutable source once while hashing, then publish only durable bytes.
def store_blob(
    source: Path, root: Path, check: Callable[[], None] | None = None,
) -> tuple[str, Path]:
    """Copy source with bounded memory; return its digest and fsynced CAS path."""

    root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".store-", dir=root)
    temporary = Path(name)
    digest = hashlib.sha256()
    written = 0

    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            while True:
                if check is not None:
                    check()

                block = input_file.read(BLOCK_BYTES)

                if not block:
                    break

                output.write(block)
                digest.update(block)
                written += len(block)
                if written % SYNC_BYTES < len(block):   # bound dirty pages on large blobs
                    output.flush()
                    os.fdatasync(output.fileno())

            output.flush()
            os.fsync(output.fileno())

        identity = digest.hexdigest()
        destination = blob_path(root, identity)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Replacement also repairs an existing file whose name survived disk corruption.
        os.replace(temporary, destination)
        sync_directory(destination.parent)
        sync_directory(root)
        return identity, destination
    finally:
        temporary.unlink(missing_ok=True)


# A complete hash mismatch is evidence of damaged bytes, unlike a transient network failure.
class BlobCorruption(ValueError):
    """Distinguish checksum failure from resumable interruption."""


# Stream an exact suffix into a retained partial file.
def download_suffix(
    partial: Path, location: str, size: int, check: Callable[[], None] | None,
) -> int:
    """Download remaining bytes from location; return the reused prefix length."""

    offset = partial.stat().st_size if partial.exists() else 0

    if offset > size:
        partial.unlink()
        offset = 0

    if offset == size and partial.exists():
        return offset

    request = Request(location)

    if offset:
        request.add_header("Range", f"bytes={offset}-")

    with urlopen(request, timeout=5) as response:
        if response.status == 200:
            offset = 0
        elif response.status == 206:
            expected = f"bytes {offset}-{size - 1}/{size}"

            if response.headers.get("Content-Range") != expected:
                raise ValueError("invalid partial response range")
        else:
            raise ValueError("unexpected blob response status")

        if int(response.headers.get("Content-Length", "-1")) != size - offset:
            raise BlobCorruption("blob response length mismatch")

        with partial.open("ab" if offset else "wb") as output:
            remaining = size - offset

            while remaining:
                if check is not None:
                    check()

                block = response.read(min(BLOCK_BYTES, remaining))

                if not block:
                    raise OSError("interrupted blob transfer")

                output.write(block)
                remaining -= len(block)

            output.flush()
            os.fsync(output.fileno())

    return offset


# Download missing suffixes and validate complete bytes before publishing a replica.
def fetch_blob(
    root: Path,
    digest: str,
    size: int,
    locations: list[str],
    check: Callable[[], None] | None = None,
    bad_source: Callable[[str | None, str], None] | None = None,
) -> Path:
    """Fetch exact hashed bytes, retaining interruptions and reporting confirmed bad copies."""

    destination = blob_path(root, digest)

    if type(size) is not int or size < 0:
        raise ValueError("invalid blob size")

    downloads = root / ".downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    partial = downloads / f"{digest}.part"
    failures: list[str] = []

    # Waiting for another transfer remains interruptible by lease revocation.
    with (downloads / f"{digest}.lock").open("a+b") as lock:
        while True:
            if check is not None:
                check()

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)

        if destination.is_file():
            if destination.stat().st_size == size and file_digest(destination, check) == digest:
                return destination

            if bad_source is not None:
                bad_source(None, digest)

        for location in locations:
            # A prefix from another peer is unverified until the complete hash succeeds.
            for attempt in range(2):
                if check is not None:
                    check()

                try:
                    reused = download_suffix(partial, location, size, check)

                    if partial.stat().st_size != size or file_digest(partial, check) != digest:
                        partial.unlink(missing_ok=True)

                        # Retry this peer from zero before blaming it for somebody else's prefix.
                        if reused and attempt == 0:
                            continue

                        raise BlobCorruption("downloaded blob hash mismatch")

                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(partial, destination)
                    sync_directory(destination.parent)
                    sync_directory(root)
                    return destination
                except (OSError, ValueError, http.client.HTTPException) as error:
                    failures.append(str(error))

                    # A timeout never removes a healthy replica from the leader's index.
                    confirmed = isinstance(error, BlobCorruption) or (
                        isinstance(error, HTTPError) and error.code in {404, 410}
                    )

                    if confirmed and bad_source is not None:
                        bad_source(location, digest)

                    break

    raise OSError("no valid blob source: " + "; ".join(failures[-3:]))


# Serialize local object publication, restore and retirement across threads and processes.
@contextmanager
def storage_transaction(root: Path, check: Callable[[], None] | None = None):
    """Hold root's storage lock until context exit; optional check cancels lock waits."""

    root.mkdir(parents=True, exist_ok=True)

    with (root / ".storage.lock").open("a+b") as lock:
        while True:
            if check is not None:
                check()

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)

        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
