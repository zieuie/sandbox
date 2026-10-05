"""Prefix every line a long-running process writes to stdout or stderr with its local time.

The leader, agents and feeder log by writing to stdout and stderr, which their launcher
points at a file. Without times, a traceback in leader.log could not be placed: the
dashboard stamped lines with when it first saw them, and lost even that on a restart.

install() wraps sys.stdout and sys.stderr in place. Each line gets an ISO 8601 local time
with milliseconds and the UTC offset, then one space:

    2026-10-04T20:31:05.123-05:00 leader listening on http://0.0.0.0:8061

It is done inside the process, not by a pipe to a stamping process or thread, so logging
can never block on a helper, and a crash's final traceback is written exactly as before.
Child processes that inherit the raw file descriptors (rather than going through these
streams) are not stamped. split() undoes the prefix for readers.
"""

from __future__ import annotations

import datetime
import io
import re
import sys
import threading
from typing import TextIO

LINES = re.compile(r"[^\n]*\n|[^\n]+")  # each line with its newline, then any unfinished tail
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:[+-]\d\d:\d\d|Z)) ")


def now() -> str:
    """Return the current local time as an ISO 8601 stamp."""

    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def split(line: str) -> tuple[float | None, str]:
    """Return (seconds since the epoch, text) for a stamped line, or (None, line) for an unstamped one."""

    match = STAMP.match(line)
    if match is None:
        return None, line
    try:
        when = datetime.datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None, line
    return when, line[match.end():]


class Stamped(io.TextIOBase):
    """A text stream that writes through to another, stamping the start of every line."""

    def __init__(self, stream: TextIO) -> None:
        super().__init__()
        self.stream = stream
        # Per stream: stdout and stderr buffer separately, so one stream's unfinished line
        # must not decide whether the other's next line is stamped.
        self.lock = threading.Lock()
        self.at_line_start = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        with self.lock:
            pieces = []
            for part in LINES.findall(text):
                if self.at_line_start:
                    pieces.append(now() + " ")
                pieces.append(part)
                self.at_line_start = part.endswith("\n")
            self.stream.write("".join(pieces))
        return len(text)

    def flush(self) -> None:
        self.stream.flush()

    def fileno(self) -> int:
        return self.stream.fileno()

    def isatty(self) -> bool:
        return self.stream.isatty()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self.stream.encoding

    @property
    def errors(self) -> str | None:  # type: ignore[override]
        return self.stream.errors


def install() -> None:
    """Stamp everything this process writes through sys.stdout and sys.stderr (idempotent)."""

    if isinstance(sys.stdout, Stamped):
        return
    sys.stdout = Stamped(sys.stdout)
    sys.stderr = Stamped(sys.stderr)
