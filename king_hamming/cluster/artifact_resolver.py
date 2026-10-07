"""Bounded, hash-verified retrieval of immutable cluster artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import uuid
from urllib.request import urlopen


def live_sources(run: dict, nodes: list[dict] | tuple = ()) -> tuple[str, ...]:
    """Return recorded and current content-addressed locations without duplicates."""
    digest = run.get("artifact_hash", "")
    recorded = run.get("artifact_location")
    sources = [recorded] if recorded else []
    sources.extend(
        node["address"].rstrip("/") + "/blobs/" + digest
        for node in nodes if node.get("address") and digest
    )
    return tuple(dict.fromkeys(sources))


# Callers read small artifacts (DP results) from the return value; large ones (a 13^9 matching
# certificate is 20 GB) stay on disk and are hashed and copied in bounded pieces.
INLINE_BYTES = 64 * 1024 * 1024
SYNC_BYTES = 256 * 1024 * 1024
LINK_BYTES = 1024**3                 # as blob_store.LINK_BYTES: a parked blob is linked, not copied


def file_digest(path: Path) -> tuple[int, str]:
    """Size and SHA-256 of path, read in 1 MiB pieces."""
    size, checksum = 0, hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            checksum.update(chunk)
    return size, checksum.hexdigest()


def contents(path: Path) -> bytes:
    """The artifact's bytes when small enough to hold, else b"" (read it from path)."""
    return path.read_bytes() if path.stat().st_size <= INLINE_BYTES else b""


def local_blob(digest: str, roots) -> Path | None:
    """The artifact's file in a blob store on this machine (content-addressed: root/aa/rest), if any."""
    for root in roots:
        candidate = Path(root) / digest[:2] / digest[2:]
        if candidate.is_file():
            return candidate
    return None


def retrieve(run: dict, output: Path, maximum: int,
             nodes: list[dict] | tuple = (), timeout: float = 60, local_roots=()) -> bytes:
    """Atomically retain an artifact from any live source after size/hash checks.

    A copy already in a blob store on this machine (local_roots) on output's filesystem is
    hash-checked and hard-linked rather than downloaded: blobs are immutable, and a 7^13 matching
    certificate is 206 GB. Returns its bytes if at most INLINE_BYTES, else b"" (the verified
    file is at output)."""
    digest = run.get("artifact_hash")
    if (not isinstance(digest, str) or len(digest) != 64 or maximum < 1 or
            not live_sources(run, nodes)):
        raise ValueError("complete run has no bounded published artifact")
    if output.exists():
        size, existing = file_digest(output)
        if size > maximum or existing != digest:
            raise ValueError(f"existing artifact differs from leader: {output}")
        return contents(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp-" + uuid.uuid4().hex)
    errors = []
    local = local_blob(digest, local_roots)
    if local is not None and local.stat().st_dev == output.parent.stat().st_dev:
        try:
            size, checksum = file_digest(local)
            if size > maximum or checksum != digest:
                raise ValueError("local blob differs from the leader's record")
            os.link(local, temporary)
            temporary.replace(output)
            return contents(output)
        except (OSError, ValueError) as error:
            errors.append(f"{local}: {error}")
        finally:
            temporary.unlink(missing_ok=True)
    elif local is not None and local.stat().st_size >= LINK_BYTES:
        # A blob parked on another drive (blob_store.park_blob: 7^13's certificate on merlin's
        # /mnt/khdata): checked, then linked by name, since neither disk has room for a copy.
        try:
            target = local.resolve()
            size, checksum = file_digest(target)
            if size > maximum or checksum != digest:
                raise ValueError("local blob differs from the leader's record")
            os.symlink(target, temporary)
            temporary.replace(output)
            return contents(output)
        except (OSError, ValueError) as error:
            errors.append(f"{local}: {error}")
        finally:
            temporary.unlink(missing_ok=True)
    try:
        for source in live_sources(run, nodes):
            temporary.unlink(missing_ok=True)
            try:
                size, checksum = 0, hashlib.sha256()
                with urlopen(source, timeout=timeout) as response, temporary.open("xb") as stream:
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > maximum:
                            raise ValueError("downloaded artifact exceeds its format bound")
                        stream.write(chunk)
                        checksum.update(chunk)
                        if size % SYNC_BYTES < len(chunk):   # write through: bound dirty pages
                            stream.flush()
                            os.fdatasync(stream.fileno())
                    stream.flush()
                    os.fsync(stream.fileno())
                if checksum.hexdigest() != digest:
                    raise ValueError("downloaded artifact has wrong hash")
                temporary.replace(output)
                return contents(output)
            except (OSError, ValueError) as error:
                errors.append(f"{source}: {error}")
        raise RuntimeError(f"no live verified artifact source: {errors}")
    finally:
        temporary.unlink(missing_ok=True)
