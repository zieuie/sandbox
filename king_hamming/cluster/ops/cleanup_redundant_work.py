#!/usr/bin/env python3
"""Reclaim completed lease copies, retaining every indexed artifact blob."""

import argparse
import json
import shlex
import sqlite3
import subprocess
from pathlib import Path


STATE = Path(__file__).resolve().parents[1] / "deployments/continuous-campaign/leader.sqlite"
ROOT = "/home/zieuie/.local/share/king_hamming/dp-1065dc8a7aa44c35bf1c16764b7a11ff"
HOSTS = (101, 102, 103, 104, 105, 106, 107, 108)

REMOTE = r'''
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
apply = sys.argv[2] == "apply"
work = root / "work"
blobs = root / "blobs"
items = json.load(sys.stdin)

def agent_running():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or proc.name == str(os.getpid()):
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, PermissionError):
            continue
        if (any(arg.endswith(b"/cluster/agent.py") for arg in argv)
                and b"run" in argv and str(root).encode() in b" ".join(argv)):
            return True
    return False

before = shutil.disk_usage(root).free
eligible = 0
removed = 0
skipped = 0
for run_id, digest, expected_size in items:
    directory = work / run_id
    artifact = blobs / digest[:2] / digest[2:]
    if (directory.is_symlink() or not directory.is_dir()
            or not artifact.is_file() or artifact.stat().st_size != expected_size):
        skipped += 1
        continue
    eligible += 1
    if apply:
        with artifact.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != digest:
            skipped += 1
            continue
        shutil.rmtree(directory)
        removed += 1

partial_bytes = 0
partial_files = 0
active = agent_running()
if not active:
    downloads = blobs / ".downloads"
    if downloads.is_dir():
        for path in downloads.iterdir():
            if path.is_file() and path.suffix in (".part", ".lock"):
                partial_bytes += path.stat().st_size
                partial_files += 1
                if apply:
                    path.unlink()

after = shutil.disk_usage(root).free
print(json.dumps({"eligible_completed_dirs": eligible, "removed_dirs": removed,
                  "skipped_missing_blob_or_size": skipped,
                  "agent_running": active, "partial_files": partial_files,
                  "partial_bytes": partial_bytes, "free_before": before,
                  "free_after": after}), flush=True)
'''


def ssh(host, command, *, input_text=None):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
        input=input_text, text=True, stdout=subprocess.PIPE, check=True,
    ).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    with sqlite3.connect(f"file:{STATE}?mode=ro", uri=True) as database:
        rows = database.execute(
            "SELECT r.run_id, r.artifact_hash, a.size, COUNT(p.node_name) "
            "FROM runs r JOIN artifacts a ON a.artifact_hash=r.artifact_hash "
            "LEFT JOIN replicas p ON p.artifact_hash=r.artifact_hash "
            "WHERE r.state='complete' GROUP BY r.run_id"
        )
        completed = {run_id: (digest, size) for run_id, digest, size, copies in rows
                     if copies >= 2 and size is not None}

    for suffix in HOSTS:
        host = f"192.168.4.{suffix}"
        listing = ssh(host, f"find {shlex.quote(ROOT + '/work')} -mindepth 1 "
                            "-maxdepth 1 -type d -printf '%f\\n'")
        manifest = [[run_id, *completed[run_id]] for run_id in listing.splitlines()
                    if run_id in completed]
        command = (f"python3 -c {shlex.quote(REMOTE)} "
                   f"{shlex.quote(ROOT)} {'apply' if arguments.apply else 'preview'}")
        result = ssh(host, command, input_text=json.dumps(manifest))
        print(host, result.strip(), flush=True)


if __name__ == "__main__":
    main()
