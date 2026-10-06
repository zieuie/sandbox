#!/usr/bin/env python3
"""Reconnect Wi-Fi when a machine is offline but a saved Wi-Fi network is in range.

A standalone system service, independent of King Hamming and of any particular network: it
knows no addresses, SSIDs or interface names. Every minute (wifi-watchdog.timer) it asks one
question: does the current default gateway answer, over any interface? If so, nothing happens,
whatever network the machine is on, wired or wireless. Only after three offline minutes in a
row, a confirmation ten seconds later, and only when NetworkManager can see a Wi-Fi network it
has saved credentials for, does it act: `nmcli device connect` on that Wi-Fi device, falling
back to restarting NetworkManager if that fails. At most one attempt per ten minutes.

This clears NetworkManager's "no-secrets" state, which otherwise blocks autoconnect until a
person logs in (docs/NETWORK_OUTAGE_2026-10-02.md). With no saved network in range (a machine
taken elsewhere, unplugged, or with Wi-Fi switched off) it does nothing.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Optional

STATE_DIR = Path("/run/wifi-watchdog")
FAILURES_REQUIRED = 3
ACTION_COOLDOWN_SECONDS = 600
CONFIRMATION_DELAY_SECONDS = 10

Runner = Callable[..., subprocess.CompletedProcess]


def run(command: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    """Run a command, capturing text; a missing tool or timeout reads as a failure."""

    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))


def gateways(runner: Runner = run) -> list[tuple[str, str]]:
    """(gateway, device) for every IPv4 default route."""

    found = []
    for line in runner(["ip", "-4", "route", "show", "default"]).stdout.splitlines():
        words = line.split()
        if "via" in words and "dev" in words:
            found.append((words[words.index("via") + 1], words[words.index("dev") + 1]))
    return found


def online(runner: Runner = run) -> bool:
    """True when any default gateway answers a ping, or at least answers ARP (some routers drop ICMP)."""

    for gateway, device in gateways(runner):
        if runner(["ping", "-n", "-c", "1", "-W", "2", "-I", device, gateway], timeout=5).returncode == 0:
            return True
        if "REACHABLE" in runner(["ip", "neigh", "show", "to", gateway, "dev", device]).stdout:
            return True
    return False


def split_terse(line: str) -> list[str]:
    """Split one `nmcli -t` line on unescaped colons, undoing nmcli's backslash escapes."""

    fields, current, escaped = [], [], False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    fields.append("".join(current))
    return fields


def known_network_device(runner: Runner = run) -> Optional[str]:
    """The Wi-Fi device that can see a network NetworkManager has a saved connection for, if any."""

    devices = [fields[0] for fields in map(split_terse, runner(
        ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"]).stdout.splitlines())
        if len(fields) == 2 and fields[1] == "wifi"]
    saved = set()
    for fields in map(split_terse, runner(["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"]).stdout.splitlines()):
        if len(fields) == 2 and fields[1] == "802-11-wireless":
            ssid = runner(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", fields[0]]).stdout.strip()
            if ssid:
                saved.add(ssid)
    for device in devices:
        visible = runner(["nmcli", "-t", "-f", "SSID", "device", "wifi", "list", "ifname", device,
                          "--rescan", "yes"], timeout=40).stdout.splitlines()
        if saved & {split_terse(line)[0] for line in visible}:
            return device
    return None


def reconnect(device: str, runner: Runner = run) -> str:
    """Let NetworkManager pick the best saved connection for device; restart it if that fails."""

    if runner(["nmcli", "device", "connect", device], timeout=90).returncode == 0:
        return f"reconnected {device}"
    runner(["systemctl", "restart", "NetworkManager.service"], timeout=60)
    return f"nmcli could not connect {device}; restarted NetworkManager"


def read_number(path: Path) -> int:
    try:
        return max(0, int(path.read_text().strip()))
    except (FileNotFoundError, ValueError):
        return 0


def check(
    state_dir: Path = STATE_DIR,
    probe: Callable[[], bool] = online,
    find: Callable[[], Optional[str]] = known_network_device,
    act: Callable[[str], str] = reconnect,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> str:
    """One minute's check: three offline checks, a confirmation, a saved network in range, a cooldown."""

    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    with (state_dir / "lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        failures_path = state_dir / "failures"
        action_path = state_dir / "last-action"
        if probe():
            failures_path.write_text("0\n")
            return "online"

        failures = read_number(failures_path) + 1
        failures_path.write_text(f"{failures}\n")
        if failures < FAILURES_REQUIRED:
            return f"offline {failures}/{FAILURES_REQUIRED}"
        last_action = read_number(action_path)
        if last_action and clock() - last_action < ACTION_COOLDOWN_SECONDS:
            return "offline; waiting out the cooldown"

        pause(CONFIRMATION_DELAY_SECONDS)
        if probe():
            failures_path.write_text("0\n")
            return "online again before acting"
        device = find()
        if device is None:
            return "offline; no saved Wi-Fi network in range, nothing to do"

        # Record the attempt first: even a failed one must not repeat for ten minutes.
        action_path.write_text(f"{max(1, int(clock()))}\n")
        failures_path.write_text("0\n")
        return act(device)


def main() -> int:
    try:
        result = check()
    except Exception as error:
        print(f"wifi-watchdog: {error}", file=sys.stderr, flush=True)
        return 1
    if result != "online":
        print(f"wifi-watchdog: {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
