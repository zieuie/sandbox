#!/usr/bin/env python3
"""Turn archived matching certificates that are also in this machine's blob store into hard links.

Before 2026-10-06 a finished certificate existed twice on merlin: the agent's blob-store copy and
the feeder's copy in matching-results/ (29^7: 2 x 32 GB). Both are immutable. For each archive
whose run's artifact is in a local blob store on the same filesystem, this checks that both files
hash to the leader's recorded artifact hash, then replaces the archive with a link to the blob.
Dry run unless --apply. Files that are already one file, differ, or have no local blob are left alone.

Example: python3 cluster/ops/link_certificates.py --state cluster/deployments/continuous-campaign --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, required=True, help="deployment directory (leader.sqlite, manifest.json)")
    parser.add_argument("--min-bytes", type=int, default=1024**3, help="skip archives smaller than this")
    parser.add_argument("--apply", action="store_true", help="replace archives with links (default: report only)")
    arguments = parser.parse_args()
    state = arguments.state
    manifest = json.loads((state / "manifest.json").read_text())
    roots = [Path(worker["root"]) / "blobs" for worker in manifest.get("workers", [])
             if worker.get("root") and (Path(worker["root"]) / "blobs").is_dir()]
    roots = list(dict.fromkeys(roots))
    connection = sqlite3.connect(f"file:{state / 'leader.sqlite'}?mode=ro", uri=True)
    saved = 0
    for archive in sorted((state / "matching-results").glob("*.khmatch")):
        size = archive.stat().st_size
        if size < arguments.min_bytes:
            continue
        run_id = archive.stem.split("_", 2)[2]
        row = connection.execute("SELECT artifact_hash FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or not row[0]:
            print(f"skip {archive.name}: no recorded artifact for run {run_id}")
            continue
        digest = row[0]
        blob = next((root / digest[:2] / digest[2:] for root in roots
                     if (root / digest[:2] / digest[2:]).is_file()), None)
        if blob is None:
            print(f"skip {archive.name}: not in a local blob store")
            continue
        if blob.stat().st_ino == archive.stat().st_ino:
            print(f"ok   {archive.name}: already one file")
            continue
        if blob.stat().st_dev != archive.stat().st_dev:
            print(f"skip {archive.name}: blob is on another filesystem")
            continue
        if not arguments.apply:
            print(f"would link {archive.name} to {blob} ({size / 1e9:.1f} GB freed)")
            saved += size
            continue
        if sha256(archive) != digest or sha256(blob) != digest:
            print(f"skip {archive.name}: a copy does not match the recorded hash", file=sys.stderr)
            continue
        temporary = archive.with_name(archive.name + ".link-tmp")
        temporary.unlink(missing_ok=True)
        os.link(blob, temporary)
        os.replace(temporary, archive)
        directory = os.open(archive.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        saved += size
        print(f"linked {archive.name} to {blob} ({size / 1e9:.1f} GB freed)")
    print(f"{'freed' if arguments.apply else 'would free'} {saved / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
