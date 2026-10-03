"""Solver-neutral GPU discovery, registration records, and per-host device locks."""

from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "cuda" / "kh_cuda_probe"
LOCK_DIRECTORY = Path(os.environ.get("KH_GPU_LOCK_DIR", "/tmp"))
# Driver/context/display headroom kept free on every advertised device.
DEVICE_RESERVE_BYTES = 256 * 1024**2
MAX_DEVICES = 16


def detect(probe: Path = PROBE, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Return usable CUDA devices via the driver-API probe; [] if absent, disabled or broken."""

    if os.environ.get("KH_DISABLE_GPU") == "1" or not probe.exists():
        return []
    try:
        completed = subprocess.run([str(probe)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    devices = []
    for line in completed.stdout.splitlines():
        try:
            devices.append(json.loads(line))
        except ValueError:
            return []
    try:
        return normalized(devices)
    except ValueError:
        return []


def normalized(raw: Any) -> list[dict[str, Any]]:
    """Validate an agent-supplied GPU list; reject malformed records."""

    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_DEVICES:
        raise ValueError("invalid GPU list")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid GPU record")
        index, total, arch = item.get("index"), item.get("total_bytes"), item.get("arch", 0)
        name = str(item.get("name", ""))[:96]
        if (type(index) is not int or not 0 <= index < MAX_DEVICES or index in seen or
                type(total) is not int or total <= 0 or type(arch) is not int or arch < 0):
            raise ValueError("invalid GPU identity")
        seen.add(index)
        result.append({"index": index, "name": name, "arch": arch, "total_bytes": total})
    return sorted(result, key=lambda item: item["index"])


def from_record(node: Any) -> list[dict[str, Any]]:
    """Read a node row's stored GPU list (gpus_json), tolerating legacy rows."""

    try:
        raw = node["gpus_json"]
    except (KeyError, IndexError, TypeError):
        return []
    try:
        return normalized(json.loads(raw or "[]"))
    except ValueError:
        return []


def usable_bytes(device: dict[str, Any]) -> int:
    """Device memory a single exclusive lease may plan to use."""

    return max(0, int(device["total_bytes"]) - DEVICE_RESERVE_BYTES)


def choose(devices: list[dict[str, Any]], busy: set[int], required: int) -> int | None:
    """Return the smallest free device index that fits required bytes, else None."""

    for device in sorted(devices, key=lambda item: (item["total_bytes"], item["index"])):
        if device["index"] not in busy and usable_bytes(device) >= required:
            return device["index"]
    return None


class DeviceLock:
    """Host-wide exclusive use of one GPU, shared by every agent and solver process."""

    def __init__(self, index: int) -> None:
        self.path = LOCK_DIRECTORY / f"kh-gpu-{int(index)}.lock"
        self.descriptor: int | None = None

    def acquire(self, timeout: float, should_stop: Callable[[], bool] = lambda: False) -> bool:
        """Wait up to timeout seconds; return False on timeout or a stop request."""

        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.descriptor = descriptor
                return True
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(descriptor)
                    raise
            if should_stop() or time.monotonic() >= deadline:
                os.close(descriptor)
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self) -> "DeviceLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# A device that recently failed to initialize is skipped for opportunistic work.
def mark_unavailable(index: int) -> None:
    try:
        (LOCK_DIRECTORY / f"kh-gpu-{int(index)}.unavailable").write_text(str(time.time()))
    except OSError:
        pass


def recently_unavailable(index: int, seconds: float = 600.0) -> bool:
    try:
        stamp = float((LOCK_DIRECTORY / f"kh-gpu-{int(index)}.unavailable").read_text())
    except (OSError, ValueError):
        return False
    return time.time() - stamp < seconds
