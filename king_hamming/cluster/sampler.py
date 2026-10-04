"""Opt-in CPU profiler for a long-running process: which Python stacks are using the CPU.

Every INTERVAL seconds it reads each thread's CPU ticks from /proc, and for a thread
that ran since the last sample it charges that thread's current stack with the ticks
it used. Blocked or sleeping threads cost nothing, so waiting in the database or on a
socket does not hide the code that really burns CPU. Folded stacks (leaf last, the
format flame-graph tools read) are written to a file every few seconds.

Enable in the leader with KH_LEADER_PROFILE=1. Overhead is about two percent.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from pathlib import Path

INTERVAL = 0.1
WRITE_SECONDS = 20.0
DEPTH = 7


def thread_ticks(tid: int) -> int:
    """Return utime+stime clock ticks of thread tid, or 0 if it has gone."""

    try:
        text = Path(f"/proc/self/task/{tid}/stat").read_text()
    except OSError:
        return 0
    fields = text[text.rindex(")") + 2:].split()
    return int(fields[11]) + int(fields[12])


def fold(frame, depth: int = DEPTH) -> str:
    """Return frame's stack as 'outer;...;leaf' of file:function, at most depth deep."""

    names = []
    while frame is not None and len(names) < depth:
        code = frame.f_code
        names.append(f"{Path(code.co_filename).name}:{code.co_name}:{frame.f_lineno}"
                     if not names else f"{Path(code.co_filename).name}:{code.co_name}")
        frame = frame.f_back
    return ";".join(reversed(names))


class CpuSampler(threading.Thread):
    """Charge each thread's CPU use to the stack it was running."""

    def __init__(self, output: Path, interval: float = INTERVAL, write_seconds: float = WRITE_SECONDS) -> None:
        super().__init__(name="cpu-sampler", daemon=True)
        self.output, self.interval, self.write_seconds = output, interval, write_seconds
        self.weights: collections.Counter[str] = collections.Counter()
        self.started = time.time()
        self.stop_event = threading.Event()

    def sample(self, previous: dict[int, int]) -> None:
        """Record one round: charge every thread that used CPU since previous."""

        mine = threading.get_ident()
        for ident, frame in sys._current_frames().items():
            if ident == mine:
                continue
            tid = next((t.native_id for t in threading.enumerate() if t.ident == ident), None)
            if tid is None:
                continue
            ticks = thread_ticks(tid)
            used = ticks - previous.get(tid, ticks)
            previous[tid] = ticks
            if used > 0:
                self.weights[fold(frame)] += used

    def write(self) -> None:
        total = sum(self.weights.values()) or 1
        lines = [f"# cpu ticks by stack since {time.strftime('%H:%M:%S', time.localtime(self.started))}; "
                 f"total {sum(self.weights.values())} ticks (1 tick = 10 ms); stacks are outer;...;leaf"]
        for stack, ticks in self.weights.most_common(150):
            lines.append(f"{ticks} {100 * ticks / total:.1f}% {stack}")
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.replace(self.output)

    def run(self) -> None:
        previous: dict[int, int] = {}
        next_write = time.monotonic() + self.write_seconds
        while not self.stop_event.wait(self.interval):
            try:
                self.sample(previous)
                if time.monotonic() >= next_write:
                    self.write()
                    next_write = time.monotonic() + self.write_seconds
            except Exception:  # profiling must never disturb the process it watches
                pass


def start_if_requested(output: Path) -> CpuSampler | None:
    """Start a sampler writing to output when KH_LEADER_PROFILE=1; return it, or None."""

    if os.environ.get("KH_LEADER_PROFILE") != "1":
        return None
    sampler = CpuSampler(output)
    sampler.start()
    return sampler
