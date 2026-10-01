#!/usr/bin/env python3
"""Run one dashboard job detached from the dashboard, recording its outcome.

Usage: jobrunner.py JOB.json  (written by jobs.Jobs.start; not for manual use)

It forks once and the parent exits at once, so the runner is adopted by init
rather than lingering as a child (and later a zombie) of the dashboard.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def update(path: Path, **changes) -> None:
    record = json.loads(path.read_text())
    record.update(changes)
    temporary = path.with_suffix(".runner.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(record, stream, indent=2)
    temporary.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if os.fork():
        os._exit(0)
    os.setsid()
    record = json.loads(path.read_text())
    start = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()[19]
    update(path, runner_pid=os.getpid(), runner_start=start)
    log_path = path.with_suffix(".log")
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as log:
        log.write(f"$ {' '.join(record['argv'])}\n".encode())
        log.flush()
        try:
            completed = subprocess.run(record["argv"], cwd=record["cwd"], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            code = completed.returncode
        except OSError as error:
            log.write(f"could not start: {error}\n".encode())
            code = 127
        log.write(f"\n[exit status {code}]\n".encode())
    update(path, status="ok" if code == 0 else "failed", exit_code=code, finished=time.time())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
