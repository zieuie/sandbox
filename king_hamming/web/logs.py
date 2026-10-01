"""Follow campaign log files and stamp new lines with the time they were seen.

The leader and feeder logs carry no timestamps. On its first read a watcher
parses the existing file as an untimed baseline; afterwards every new event is
stamped with the interval in which it appeared (between two polls). Nothing is
persisted, so a dashboard restart starts a new baseline.
"""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import re

EXCEPTION = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))(?::\s?(.*))?$")
REQUEST = re.compile(r"^Exception occurred during processing of request from \('([^']+)', \d+\)")
SEPARATOR = re.compile(r"^-{20,}$")
# Clients hanging up mid-response; noisy and normally harmless.
BENIGN = {"BrokenPipeError", "ConnectionResetError"}
MAX_BASELINE_BYTES = 8 * 1024**2
MAX_POLL_BYTES = 4 * 1024**2


class LeaderLogParser:
    """Turn leader.log lines into exception, restart, and scheduler events."""

    def __init__(self) -> None:
        self.client: str | None = None
        self.last: dict | None = None

    @staticmethod
    def baseline(events: list[dict]) -> list[dict]:
        """Keep only the current leader session: from its last start onward."""
        starts = [index for index, event in enumerate(events) if event["kind"] == "leader_start"]
        return events[starts[-1]:] if starts else events

    def exception(self, match: re.Match) -> dict:
        kind = match.group(1)
        return {"kind": "leader_exception", "type": kind, "message": (match.group(2) or "")[:300],
                "benign": kind.rsplit(".", 1)[-1] in BENIGN}

    def feed(self, lines: list[str]) -> list[dict]:
        events = []
        for line in lines:
            request = REQUEST.match(line)
            if request:
                self.client, self.last = request.group(1), None
                continue
            if self.client is not None:
                # Inside one failed request: keep the final exception of the block.
                match = EXCEPTION.match(line)
                if match:
                    self.last = self.exception(match)
                elif SEPARATOR.match(line):
                    if self.last:
                        events.append({**self.last, "client": self.client})
                    self.client, self.last = None, None
                continue
            if line.startswith("leader listening on"):
                events.append({"kind": "leader_start", "message": line.strip()})
            elif line.startswith("scheduler transaction failed"):
                events.append({"kind": "scheduler_retry", "message": line.strip()})
            else:
                match = EXCEPTION.match(line)
                if match:
                    events.append(self.exception(match))
        return events


class FeederLogParser:
    """Turn feeder.log lines into reconcile results and retried errors."""

    def feed(self, lines: list[str]) -> list[dict]:
        events = []
        for line in lines:
            text = line.strip()
            if text.startswith("{"):
                try:
                    events.append({"kind": "reconcile", "result": json.loads(text)})
                except ValueError:
                    continue
            elif text.startswith("continuous campaign will retry:"):
                events.append({"kind": "feeder_error",
                               "message": text.split(":", 1)[1].strip()[:300]})
            elif text.startswith("continuous_campaign.py:") or text.startswith("Traceback"):
                events.append({"kind": "feeder_error", "message": text[:300]})
        return events


class LogWatcher:
    """Incrementally read one append-only log, tolerating rotation."""

    def __init__(self, path: Path, parser, keep: int = 2000) -> None:
        self.path = path
        self.parser = parser
        self.keep = keep
        self.identity: tuple[int, int] | None = None
        self.offset = 0
        self.partial = ""
        self.baseline: deque[dict] = deque(maxlen=keep)
        self.observed: deque[dict] = deque(maxlen=keep)
        self.started: float | None = None  # first poll, i.e. when observation began
        self.last_poll: float | None = None

    def poll(self, now: float) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        identity = (stat.st_dev, stat.st_ino)
        first = self.started is None
        if identity != self.identity or stat.st_size < self.offset:
            self.offset = max(0, stat.st_size - MAX_BASELINE_BYTES) if first else 0
            self.partial = ""
            self.identity = identity
        if stat.st_size > self.offset:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                limit = MAX_BASELINE_BYTES if first else MAX_POLL_BYTES
                data = stream.read(min(stat.st_size - self.offset, limit))
            self.offset += len(data)
            text = self.partial + data.decode("utf-8", errors="replace")
            lines = text.split("\n")
            self.partial = lines.pop()
            events = self.parser.feed(lines)
            if first and hasattr(self.parser, "baseline"):
                events = self.parser.baseline(events)
            for event in events:
                if first:
                    event["time"] = None
                    self.baseline.append(event)
                else:
                    event["time"] = now
                    event["after"] = self.last_poll
                    self.observed.append(event)
        if first:
            self.started = now
        self.last_poll = now


def process_info(pid: int, *fragments: str) -> dict | None:
    """Return start time and command for a live local process whose command line
    contains every fragment, or None. Reads /proc only."""
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    arguments = [part.decode(errors="replace") for part in command if part]
    joined = " ".join(arguments)
    if not all(fragment in joined for fragment in fragments):
        return None
    try:
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                    if line.startswith("btime "))
        started = boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        started = None
    return {"pid": pid, "started": started, "command": arguments}


def find_process(*fragments: str) -> dict | None:
    """Find the newest local process whose command line contains every fragment."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            info = process_info(int(entry.name), *fragments)
            if info:
                found.append(info)
    return max(found, key=lambda item: item["started"] or 0, default=None)
