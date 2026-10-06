#!/usr/bin/env python3
"""Test the standalone Wi-Fi watchdog: what counts as online, when it acts, and what it runs."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "wifi_watchdog.py"
SPEC = importlib.util.spec_from_file_location("wifi_watchdog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def fake_runner(responses: dict[tuple[str, ...], tuple[int, str]], calls: list | None = None):
    """A runner answering by command prefix; unknown commands fail with no output."""

    def runner(command, timeout=20):
        if calls is not None:
            calls.append(tuple(command))
        for prefix, (code, output) in responses.items():
            if tuple(command[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(command, code, output, "")
        return subprocess.CompletedProcess(command, 1, "", "")
    return runner


ROUTE = "default via 192.168.4.1 dev wlp3s0 proto dhcp src 192.168.4.103 metric 600\n"


class OnlineTests(unittest.TestCase):
    def test_gateway_answering_ping_is_online(self) -> None:
        runner = fake_runner({("ip", "-4", "route"): (0, ROUTE), ("ping",): (0, "")})
        self.assertEqual(watchdog.gateways(runner), [("192.168.4.1", "wlp3s0")])
        self.assertTrue(watchdog.online(runner))

    def test_gateway_dropping_ping_but_answering_arp_is_online(self) -> None:
        runner = fake_runner({("ip", "-4", "route"): (0, ROUTE), ("ping",): (1, ""),
                              ("ip", "neigh"): (0, "192.168.4.1 lladdr c0:6f:98:00:00:01 REACHABLE\n")})
        self.assertTrue(watchdog.online(runner))

    def test_no_default_route_is_offline(self) -> None:
        # Wi-Fi gone; the wired cluster link has no gateway, so it doesn't count.
        runner = fake_runner({("ip", "-4", "route"): (0, ""), ("ping",): (0, "")})
        self.assertFalse(watchdog.online(runner))

    def test_any_network_counts(self) -> None:
        # A different network, over Ethernet: no King Hamming addresses are involved.
        runner = fake_runner({("ip", "-4", "route"): (0, "default via 10.0.0.1 dev eth0\n"),
                              ("ping", "-n", "-c", "1", "-W", "2", "-I", "eth0", "10.0.0.1"): (0, "")})
        self.assertTrue(watchdog.online(runner))


class KnownNetworkTests(unittest.TestCase):
    def runner(self, visible: str):
        return fake_runner({
            ("nmcli", "-t", "-f", "DEVICE,TYPE", "device"): (0, "wlp3s0:wifi\nenp0s31f6:ethernet\np2p-dev-wlp3s0:wifi-p2p\n"),
            ("nmcli", "-t", "-f", "UUID,TYPE", "connection", "show"): (0, "u1:802-11-wireless\nu2:802-3-ethernet\n"),
            ("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "u1"): (0, "Cafe: Guest\n"),
            ("nmcli", "-t", "-f", "SSID", "device", "wifi", "list"): (0, visible),
        })

    def test_saved_network_in_range(self) -> None:
        # nmcli -t escapes colons in SSIDs.
        self.assertEqual(watchdog.known_network_device(self.runner("Neighbours\nCafe\\: Guest\n")), "wlp3s0")

    def test_no_saved_network_in_range(self) -> None:
        self.assertIsNone(watchdog.known_network_device(self.runner("Neighbours\nCafe\n")))

    def test_reconnect_falls_back_to_restarting_network_manager(self) -> None:
        calls = []
        result = watchdog.reconnect("wlp3s0", fake_runner({("nmcli", "device", "connect"): (1, "")}, calls))
        self.assertIn("restarted NetworkManager", result)
        self.assertEqual(calls[-1][:2], ("systemctl", "restart"))
        calls.clear()
        self.assertEqual(watchdog.reconnect("wlp3s0", fake_runner({("nmcli", "device", "connect"): (0, "")}, calls)),
                         "reconnected wlp3s0")
        self.assertEqual(len(calls), 1)


class CheckTests(unittest.TestCase):
    def call(self, state, probe, find=lambda: "wlp3s0", acted=None, now=lambda: 1000):
        acted = [] if acted is None else acted
        return watchdog.check(state, probe, find, lambda device: acted.append(device) or f"reconnected {device}",
                              now, lambda _: None)

    def test_online_clears_the_streak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, acted = Path(directory), []
            for _ in range(2):
                self.call(state, lambda: False, acted=acted)
            self.assertEqual(self.call(state, lambda: True, acted=acted), "online")
            self.assertEqual(watchdog.read_number(state / "failures"), 0)
            self.assertEqual(acted, [])

    def test_three_offline_minutes_then_reconnect_with_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, acted, now = Path(directory), [], [1000]
            call = lambda: self.call(state, lambda: False, acted=acted, now=lambda: now[0])
            self.assertEqual([call(), call(), call()], ["offline 1/3", "offline 2/3", "reconnected wlp3s0"])
            self.assertEqual([call(), call(), call()], ["offline 1/3", "offline 2/3", "offline; waiting out the cooldown"])
            now[0] += 600
            self.assertEqual(call(), "reconnected wlp3s0")
            self.assertEqual(acted, ["wlp3s0", "wlp3s0"])

    def test_no_saved_network_in_range_does_nothing(self) -> None:
        # Taken to another place with no known Wi-Fi: never thrashes.
        with tempfile.TemporaryDirectory() as directory:
            state, acted = Path(directory), []
            results = [self.call(state, lambda: False, find=lambda: None, acted=acted) for _ in range(6)]
            self.assertEqual(results[2:], ["offline; no saved Wi-Fi network in range, nothing to do"] * 4)
            self.assertEqual(acted, [])

    def test_confirmation_avoids_acting_on_a_blip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state, acted = Path(directory), []
            probes = iter((False, False, False, True))
            for _ in range(2):
                self.call(state, lambda: next(probes), acted=acted)
            self.assertEqual(self.call(state, lambda: next(probes), acted=acted), "online again before acting")
            self.assertEqual(acted, [])


if __name__ == "__main__":
    unittest.main()
