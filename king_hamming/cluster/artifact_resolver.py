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


def retrieve(run: dict, output: Path, maximum: int,
             nodes: list[dict] | tuple = (), timeout: float = 60) -> bytes:
    """Atomically retain an artifact from any live source after size/hash checks."""
    digest = run.get("artifact_hash")
    if (not isinstance(digest, str) or len(digest) != 64 or maximum < 1 or
            not live_sources(run, nodes)):
        raise ValueError("complete run has no bounded published artifact")
    if output.exists():
        raw = output.read_bytes()
        if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"existing artifact differs from leader: {output}")
        return raw
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp-" + uuid.uuid4().hex)
    errors = []
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
                    stream.flush()
                    os.fsync(stream.fileno())
                if checksum.hexdigest() != digest:
                    raise ValueError("downloaded artifact has wrong hash")
                temporary.replace(output)
                return output.read_bytes()
            except (OSError, ValueError) as error:
                errors.append(f"{source}: {error}")
        raise RuntimeError(f"no live verified artifact source: {errors}")
    finally:
        temporary.unlink(missing_ok=True)
