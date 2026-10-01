"""Append-only audit log of security-relevant dashboard actions (JSON lines)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

_LOCK = threading.Lock()


class Audit:
    def __init__(self, state: Path, clock=time.time) -> None:
        self.path = state / "audit.jsonl"
        self.clock = clock

    def record(self, action: str, user: str | None, address: str | None, outcome: str, **details) -> dict:
        entry = {"time": self.clock(), "action": action, "user": user, "address": address,
                 "outcome": outcome, **details}
        line = json.dumps(entry, sort_keys=True) + "\n"
        with _LOCK:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "a") as stream:
                stream.write(line)
        return entry

    def recent(self, limit: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-limit:]
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return entries
