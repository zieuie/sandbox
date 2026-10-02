#!/usr/bin/env python3
"""Recover Pellinore's Wi-Fi when every local network path stays unreachable."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

STATE_DIR = Path("/run/pellinore-network-watchdog")
INTERFACE = "wlp59s0"
PEERS = ("192.168.4.1", "192.168.4.151", "192.168.4.101")
FAILURES_REQUIRED = 3
RESTART_COOLDOWN_SECONDS = 600
CONFIRMATION_DELAY_SECONDS = 10


def reachable() -> bool:
    """Accept any reachable gateway or cluster peer, not an Internet dependency."""

    for peer in PEERS:
        try:
            result = subprocess.run(
                ["/usr/bin/ping", "-n", "-I", INTERFACE, "-c", "1", "-W", "2", peer],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def restart_network_manager() -> None:
    """Restart only the network service; never reboot or touch campaign processes."""

    subprocess.run(
        ["/usr/bin/systemctl", "restart", "NetworkManager.service"],
        check=True, timeout=45,
    )


def read_number(path: Path) -> int:
    try:
        return max(0, int(path.read_text().strip()))
    except (FileNotFoundError, ValueError):
        return 0


def check(
    state_dir: Path = STATE_DIR,
    probe: Callable[[], bool] = reachable,
    restart: Callable[[], None] = restart_network_manager,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> str:
    """Require three failed minute checks, reconfirm, and rate-limit restarts."""

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    with (state_dir / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        failures_path = state_dir / "failures"
        restart_path = state_dir / "last-restart"
        if probe():
            failures_path.write_text("0\n")
            return "healthy"

        failures = read_number(failures_path) + 1
        failures_path.write_text(f"{failures}\n")
        if failures < FAILURES_REQUIRED:
            return f"failure {failures}/{FAILURES_REQUIRED}"
        last_restart = read_number(restart_path)
        if last_restart and clock() - last_restart < RESTART_COOLDOWN_SECONDS:
            return "cooldown"

        pause(CONFIRMATION_DELAY_SECONDS)
        if probe():
            failures_path.write_text("0\n")
            return "recovered before restart"

        # Record the attempt first: even a failed restart must not loop rapidly.
        restart_path.write_text(f"{int(clock())}\n")
        failures_path.write_text("0\n")
        restart()
        return "restarted NetworkManager"


def main() -> int:
    try:
        result = check()
    except Exception as error:
        print(f"pellinore-network-watchdog: {error}", file=sys.stderr, flush=True)
        return 1
    if result != "healthy":
        print(f"pellinore-network-watchdog: {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
