"""Matching certificates archived on another machine (a marker file next to the local path).

7^13's certificate (206 GB) left merlin on 2026-10-14 to make room for 3^23's. Where the local path
`.../matching-results/7_13_<run>.khmatch` no longer exists, `<path>.remote` (JSON) says where the
bytes are: {"host": "192.168.4.156", "path": "/home/.../<sha256>", "bytes": N, "sha256": "..."}.
The result table and the dashboard then check the certificate over ssh (size, SHA-256 of the whole
file computed there, and the header read here) instead of reading a local file.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess

HEADER_BYTES = 4096


def marker(path: Path) -> dict | None:
    """The archive record for a certificate that is not at path, else None."""
    record = path.with_name(path.name + ".remote")
    if path.exists() or not record.is_file():
        return None
    data = json.loads(record.read_text())
    for name in ("host", "path", "bytes", "sha256"):
        if name not in data:
            raise ValueError(f"incomplete remote certificate record: {record}")
    return data


def ssh(host: str, command: str, timeout: float = 7200.0) -> bytes:
    completed = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if completed.returncode != 0:
        raise ValueError(f"ssh {host} failed: {completed.stderr.decode(errors='replace').strip()[:200]}")
    return completed.stdout


def verified_header(record: dict, artifact_hash: str) -> tuple[io.BytesIO, int]:
    """Check the archived file's size and whole-file SHA-256 on its host; return its first bytes and size."""
    quoted = "'" + record["path"].replace("'", "'\\''") + "'"
    size = int(ssh(record["host"], f"stat -c %s {quoted}").split()[0])
    digest = ssh(record["host"], f"sha256sum {quoted}").split()[0].decode()
    if size != record["bytes"] or digest != record["sha256"] or digest != artifact_hash:
        raise ValueError(f"remote matching certificate differs from its record: {record['host']}:{record['path']}")
    return io.BytesIO(ssh(record["host"], f"head -c {HEADER_BYTES} {quoted}")), size
