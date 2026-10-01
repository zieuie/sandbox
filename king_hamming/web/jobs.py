"""Long-running process jobs that survive dashboard restarts.

Each job is a JSON record plus a log in the dashboard's private state
directory. A detached `jobrunner.py` process runs the job's fixed argv and
writes the exit status back into the record, so a job keeps running (and its
outcome is recorded) even if the dashboard restarts meanwhile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time

RUNNER = Path(__file__).resolve().parent / "jobrunner.py"
TAIL_BYTES = 24 * 1024
_GIT_CACHE: dict[str, tuple[float, dict]] = {}


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2)
    temporary.replace(path)


def process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def git_state(root: Path, ttl: float = 10.0) -> dict:
    """Summarize the working tree that an upgrade would deploy (cached briefly)."""
    key = str(root)
    cached = _GIT_CACHE.get(key)
    if cached and time.time() - cached[0] < ttl:
        return cached[1]
    try:
        def git(*arguments):
            return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True,
                                  text=True, timeout=10, check=True).stdout
        state = {"head": git("rev-parse", "--short", "HEAD").strip(),
                 "subject": git("log", "-1", "--format=%s").strip(),
                 "dirty": [line[3:] for line in git("status", "--porcelain", "--", ".").splitlines()]}
    except (OSError, subprocess.SubprocessError):
        state = {"head": None, "subject": None, "dirty": [], "error": "git unavailable"}
    _GIT_CACHE[key] = (time.time(), state)
    return state


class Jobs:
    def __init__(self, directory: Path, python: str = sys.executable) -> None:
        self.directory = directory
        self.python = python
        self.lock = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)

    def record(self, job_id: str) -> dict | None:
        path = self.directory / f"{job_id}.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        if record["status"] == "running":
            pid, start = record.get("runner_pid"), record.get("runner_start")
            if pid and process_start(pid) != start:
                # The runner died without recording an outcome (e.g. machine reboot).
                record["status"] = "lost"
        return record

    def tail(self, job_id: str, limit: int = TAIL_BYTES) -> str:
        path = self.directory / f"{job_id}.log"
        if not path.exists():
            return ""
        with path.open("rb") as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")

    def list(self, limit: int = 30, tails: int = 5) -> list[dict]:
        records = []
        for path in sorted(self.directory.glob("*.json"), reverse=True)[:limit]:
            record = self.record(path.stem)
            if record:
                records.append(record)
        records.sort(key=lambda item: item["started"], reverse=True)
        for record in records[:tails]:
            record["tail"] = self.tail(record["id"])
        return records

    def cancel(self, job_id: str, grace: float = 15.0) -> dict:
        """Ask a running job's process group to stop; force it after a grace period."""
        record = self.record(job_id)
        if record is None or record["status"] != "running":
            raise RuntimeError("that job is not running")
        pid, start = record.get("child_pid"), record.get("child_start")
        if not pid or process_start(pid) != start:
            raise RuntimeError("the job's process has not started or has already exited")
        (self.directory / f"{job_id}.cancel").touch()
        os.killpg(pid, signal.SIGTERM)

        def escalate():
            time.sleep(grace)
            if process_start(pid) == start:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        threading.Thread(target=escalate, daemon=True).start()
        return {"id": job_id, "signal": "SIGTERM", "force_after_seconds": grace}

    def running(self) -> dict | None:
        return next((record for record in self.list(tails=0) if record["status"] == "running"), None)

    def start(self, name: str, title: str, argv: list[str], user: str, cwd: Path) -> dict:
        with self.lock:
            if self.running():
                raise RuntimeError("another job is already running")
            job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            record = {"id": job_id, "name": name, "title": title, "argv": argv, "cwd": str(cwd),
                      "user": user, "started": time.time(), "status": "running",
                      "exit_code": None, "finished": None}
            path = self.directory / f"{job_id}.json"
            write_json(path, record)
            # The runner forks away and records its own pid; wait for that.
            subprocess.run([self.python, str(RUNNER), str(path)], cwd=str(cwd), stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                           timeout=30, check=True)
            for _ in range(500):
                current = json.loads(path.read_text())
                if current.get("runner_pid"):
                    return current
                time.sleep(0.01)
            current["status"] = "lost"
            current["error"] = "the job runner did not start"
            write_json(path, current)
            return current
