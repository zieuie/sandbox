"""Versioned immutable checkpoint manifests, capture, validation, and restore."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from blob_store import fetch_blob, file_digest, store_blob, sync_directory, valid_digest
from common import calculation_id, canonical_json
import adapters

FORMAT = "KH-CHECKPOINT-1"
DEFAULT_MAX_BYTES = 32 * 1024**3


# Validate a small manifest before trusting any filename, size, or download request.
def validate_manifest(
    manifest: dict[str, Any], specification: dict[str, Any], run_id: str,
    max_bytes: int = DEFAULT_MAX_BYTES, require_native: bool = False,
) -> None:
    """Validate manifest against specification/run_id and resource limits; return no value."""

    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("unsupported checkpoint manifest")

    if manifest.get("run_id") != run_id or manifest.get("calculation_id") != calculation_id(specification):
        raise ValueError("checkpoint calculation or run identity mismatch")

    program = specification.get("program")

    if manifest.get("program") != program:
        raise ValueError("checkpoint program mismatch")

    cursor = manifest.get("cursor")

    if type(cursor) is not int or cursor < 0:
        raise ValueError("invalid checkpoint cursor")

    sizes, expected_done, total = adapters.get(specification).checkpoint_description(
        manifest, specification, require_native)

    if type(manifest.get("done")) is not int or type(manifest.get("total")) is not int or \
            manifest["done"] != expected_done or manifest["total"] != total:
        raise ValueError("checkpoint coverage mismatch")

    records = manifest.get("files")

    if not isinstance(records, list) or len(records) != len(sizes):
        raise ValueError("checkpoint file set mismatch")

    seen: set[str] = set()
    total_bytes = 0

    for record in records:
        name = record["name"]
        size = record["size"]

        if name not in sizes or name in seen or type(size) is not int or size < 1:
            raise ValueError("invalid checkpoint file record")

        if sizes[name] is not None and size != sizes[name]:
            raise ValueError("checkpoint file size mismatch")

        if not valid_digest(record["sha256"]):
            raise ValueError("invalid checkpoint file digest")

        seen.add(name)
        total_bytes += size

    if total_bytes > max_bytes:
        raise ValueError("checkpoint exceeds leader byte limit")


# Check that the hashed native cursor actually agrees with its portable manifest.
def validate_metadata(
    manifest: dict[str, Any], paths: dict[str, Path], require_native: bool = True,
) -> None:
    """Validate files' restart metadata against manifest; require a compatible solver layout."""

    adapters.get({"program": manifest["program"]}).validate_checkpoint_metadata(manifest, paths, require_native)


# Capture only while a solver is held at a committed checkpoint handshake.
def capture_checkpoint(
    specification: dict[str, Any], run_id: str, run_directory: Path,
    storage_root: Path, cursor: int, max_bytes: int = DEFAULT_MAX_BYTES,
    check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Capture immutable files and return a manifest; caller keeps solver quiescent throughout."""

    program = specification["program"]
    manifest: dict[str, Any] = {
        "format": FORMAT, "run_id": run_id, "calculation_id": calculation_id(specification),
        "program": program, "cursor": cursor,
    }

    paths = adapters.get(specification).checkpoint_paths(specification, run_directory, cursor, manifest)

    sizes = {name: path.stat().st_size for name, path in paths.items()}
    required = sum(sizes.values()) + 1024 * 1024
    storage_root.mkdir(parents=True, exist_ok=True)

    # Admit a complete new image without deleting previous recoverable snapshots.
    if required - 1024 * 1024 > max_bytes or shutil.disk_usage(storage_root).free < required:
        raise OSError("insufficient checkpoint disk budget or free storage")

    validate_metadata(manifest, paths)
    records: list[dict[str, Any]] = []

    for name, source in paths.items():
        digest, destination = store_blob(source, storage_root, check)

        if destination.stat().st_size != sizes[name]:
            raise ValueError("checkpoint changed during capture")

        records.append({"name": name, "sha256": digest, "size": sizes[name]})

    manifest["files"] = records
    validate_manifest(manifest, specification, run_id, max_bytes, require_native=True)
    encoded = canonical_json(manifest)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=run_directory)
    temporary = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)

        store_blob(temporary, storage_root, check)
    finally:
        temporary.unlink(missing_ok=True)

    return manifest


# Retrieve and verify all immutable members before claiming a complete replica.
def fetch_checkpoint(
    task: dict[str, Any], storage_root: Path, check: Callable[[], None] | None = None,
    bad_source: Callable[[str | None, str], None] | None = None,
) -> dict[str, Path]:
    """Fetch task's manifest and files from its replica bases; return verified CAS paths."""

    manifest = task["manifest"]
    encoded = canonical_json(manifest)
    digest = calculation_id(manifest)

    if digest != task["manifest_hash"]:
        raise ValueError("checkpoint manifest hash mismatch")

    bases = [replica["address"].rstrip("/") for replica in task["sources"]]
    fetch_blob(storage_root, digest, len(encoded), [f"{base}/blobs/{digest}" for base in bases], check, bad_source)
    paths: dict[str, Path] = {}

    for record in manifest["files"]:
        identity = record["sha256"]
        paths[record["name"]] = fetch_blob(
            storage_root, identity, record["size"],
            [f"{base}/blobs/{identity}" for base in bases], check, bad_source,
        )

    validate_metadata(manifest, paths, require_native=False)
    return paths


# Install a verified checkpoint atomically in a new lease's private work directory.
def restore_checkpoint(
    task: dict[str, Any], specification: dict[str, Any], run_id: str,
    run_directory: Path, storage_root: Path, max_bytes: int = DEFAULT_MAX_BYTES,
    check: Callable[[], None] | None = None,
    bad_source: Callable[[str | None, str], None] | None = None,
) -> None:
    """Validate and restore task into empty run_directory state; preserve immutable source blobs."""

    manifest = task["manifest"]
    validate_manifest(manifest, specification, run_id, max_bytes, require_native=True)
    required = sum(record["size"] for record in manifest["files"]) + 1024 * 1024
    run_directory.mkdir(parents=True, exist_ok=True)

    # Downloaded CAS objects and mutable working files may both consume local disk.
    if shutil.disk_usage(run_directory).free < required * 2:
        raise OSError("insufficient disk space to restore checkpoint")

    paths = fetch_checkpoint(task, storage_root, check, bad_source)
    validate_metadata(manifest, paths)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=run_directory))

    try:
        for name, source in paths.items():
            target = staging / name

            # Mutable solver state must never hard-link the immutable blob store.
            with source.open("rb") as input_file, target.open("xb") as output:
                while True:
                    if check is not None:
                        check()

                    block = input_file.read(1024 * 1024)

                    if not block:
                        break

                    output.write(block)

                output.flush()
                os.fsync(output.fileno())

            record = next(record for record in manifest["files"] if record["name"] == name)

            if target.stat().st_size != record["size"] or file_digest(target, check) != record["sha256"]:
                raise ValueError("restored checkpoint hash mismatch")

        sync_directory(staging)

        destination, member = adapters.get(specification).checkpoint_destination(run_directory)
        if destination.exists():
            raise ValueError("refusing to replace existing solver work state")
        os.rename(staging if member is None else staging / member, destination)

        sync_directory(run_directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
