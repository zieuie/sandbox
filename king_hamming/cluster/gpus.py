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

    def acquire(self, timeout: float, should_stop: Callable[[], bool] = lambda: False,
                hold: str = "", skip_long: bool = False) -> bool:
        """Wait up to timeout seconds; return False on timeout or a stop request.

        hold="long" marks a holder that keeps the device for minutes or longer (block matching).
        A waiter passing skip_long=True gives up at once when it finds such a hold, so opportunistic
        work falls back to the CPU instead of queueing for its whole timeout.
        """

        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.descriptor = descriptor
                os.ftruncate(descriptor, 0)
                if hold:
                    os.pwrite(descriptor, hold.encode("ascii"), 0)
                return True
            except OSError as error:
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(descriptor)
                    raise
            if skip_long and os.pread(descriptor, 8, 0) == b"long":
                os.close(descriptor)
                return False
            if should_stop() or time.monotonic() >= deadline:
                os.close(descriptor)
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if self.descriptor is not None:
            try:
                os.ftruncate(self.descriptor, 0)  # clear any "long" hold marker before unlocking
            except OSError:
                pass
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self) -> "DeviceLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# A device that recently failed to initialize is skipped for opportunistic work.
# A host's idle CPUs can compute a tile of their own while its GPU serves the others. This
# host-wide slot limits how many such CPU-assist tiles run at once.
class AssistSlot:
    """A non-blocking claim on one of count host-wide CPU-assist slots."""

    def __init__(self, count: int = 1) -> None:
        self.count = max(0, count)
        self.descriptor: int | None = None

    def acquire(self) -> bool:
        """Claim a free slot if there is one; never waits."""

        for index in range(self.count):
            descriptor = os.open(LOCK_DIRECTORY / f"kh-cpu-assist-{index}.lock", os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                continue
            self.descriptor = descriptor
            return True
        return False

    def release(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


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


# ----- live usage samples (for the dashboard's GPU graph) ---------------------------------------

def sample(timeout: float = 3.0) -> list[dict[str, int]]:
    """Read each GPU's utilisation, memory use and temperature with nvidia-smi; [] when it is missing or fails."""

    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode:
        return []
    stats = []
    for fields in (line.replace(" ", "").split(",") for line in done.stdout.splitlines()):
        if len(fields) == 4 and all(item.isdigit() for item in fields[:3]):
            stat = {"index": fields[0], "util_percent": fields[1], "memory_used_bytes": int(fields[2]) * 1024**2}
            if fields[3].isdigit():   # "[N/A]" on cards without a sensor
                stat["temp_c"] = int(fields[3])
            stats.append(stat)
    return normalized_stats(stats)


def normalized_stats(raw: Any) -> list[dict[str, int]]:
    """Validate a usage sample list from an agent; malformed entries are dropped, not trusted."""

    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:16]:
        try:
            index, util, memory = int(item["index"]), int(item["util_percent"]), int(item["memory_used_bytes"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < 64 and 0 <= util <= 100 and 0 <= memory < 2**50:
            stat = {"index": index, "util_percent": util, "memory_used_bytes": memory}
            temperature = item.get("temp_c") if isinstance(item, dict) else None
            if type(temperature) is int and 0 <= temperature <= 150:
                stat["temp_c"] = temperature   # absent from agents before 2026-10-06
            result.append(stat)
    return result
