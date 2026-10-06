#!/usr/bin/env python3
"""Temperatures for the dashboard's heat gauges: sensor reading, validation, and the leader's samples."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import gpus  # noqa: E402
import leader  # noqa: E402
import thermal  # noqa: E402

P600 = {"index": 0, "name": "Quadro P600", "arch": 61, "total_bytes": 2 * 1024**3}


def chip(root: Path, index: int, name: str, readings: list[tuple[str, int]]) -> None:
    directory = root / f"hwmon{index}"
    directory.mkdir(parents=True)
    (directory / "name").write_text(name + "\n")
    for number, (label, millidegrees) in enumerate(readings, 1):
        (directory / f"temp{number}_input").write_text(f"{millidegrees}\n")
        if label:
            (directory / f"temp{number}_label").write_text(label + "\n")


class SensorTests(unittest.TestCase):
    def test_intel_package_and_nvme_composite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "hwmon"
            chip(root, 0, "acpitz", [("", 67000)])
            chip(root, 1, "coretemp", [("Package id 0", 62000), ("Core 0", 99000), ("Package id 1", 71500)])
            chip(root, 2, "nvme", [("Composite", 55850), ("Sensor 2", 90850)])
            chip(root, 3, "nvme", [("Composite", 41850)])
            self.assertEqual(thermal.sample(root, Path(directory) / "none"), {"cpu_c": 71.5, "nvme_c": 55.9})

    def test_amd_and_thermal_zone_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "hwmon"
            chip(root, 0, "k10temp", [("Tctl", 81250), ("Tccd1", 70000)])
            self.assertEqual(thermal.sample(root, Path(directory) / "none"), {"cpu_c": 81.2})
            zones = Path(directory) / "thermal"
            (zones / "thermal_zone3").mkdir(parents=True)
            (zones / "thermal_zone3" / "type").write_text("x86_pkg_temp\n")
            (zones / "thermal_zone3" / "temp").write_text("58000\n")
            self.assertEqual(thermal.sample(Path(directory) / "empty", zones), {"cpu_c": 58.0})
            self.assertEqual(thermal.sample(Path(directory) / "empty", Path(directory) / "none"), {})

    def test_validation(self) -> None:
        self.assertEqual(thermal.normalized({"cpu_c": 62.04, "nvme_c": 400, "gpu_c": 50, "x": 1}), {"cpu_c": 62.0})
        self.assertEqual(thermal.normalized({"cpu_c": True}), {})
        self.assertEqual(thermal.normalized("hot"), {})
        good = {"index": 0, "util_percent": 40, "memory_used_bytes": 5}
        self.assertEqual(gpus.normalized_stats([{**good, "temp_c": 55}, {**good, "index": 1, "temp_c": 999}]),
                         [{**good, "temp_c": 55}, {**good, "index": 1}])


class LeaderSampleTests(unittest.TestCase):
    def handler(self, directory: str):
        database = Path(directory) / "leader.sqlite"
        leader.initialize(database, 1800)
        return database, object.__new__(leader.make_handler(database))

    def register(self, handler, name: str, devices: list[dict]) -> None:
        handler.dispatch_post("/v1/register", {
            "node_name": name, "cpu_set": "0,1,2,3", "memory_bytes": 16 * 1024**3,
            "slots": [{"slot_id": index, "cpu_set": str(index)} for index in range(4)], "gpus": devices})

    def test_heartbeats_store_one_heat_row_a_minute_and_gpu_temperatures(self) -> None:
        leader.LAST_THERMAL.clear()
        with tempfile.TemporaryDirectory() as directory:
            database, handler = self.handler(directory)
            self.register(handler, "gpu", [P600])
            with leader.connect(database) as connection:
                session = connection.execute("SELECT session_id FROM nodes").fetchone()[0]
            beat = {"node_name": "gpu", "session_id": session, "cpu_set": "0,1,2,3"}
            handler.dispatch_post("/v1/heartbeat", beat)   # an older agent: nothing stored
            for cpu in (61.0, 62.0):                       # within a minute: one row
                handler.dispatch_post("/v1/heartbeat", {**beat, "thermal": {"cpu_c": cpu, "nvme_c": 50.0},
                                                        "gpu_stats": [{"index": 0, "util_percent": 9,
                                                                       "memory_used_bytes": 1, "temp_c": 48}]})
            with leader.connect(database) as connection:
                heat = [tuple(row) for row in connection.execute("SELECT node_name,cpu_c,nvme_c FROM thermal_samples")]
                gpu = [row[0] for row in connection.execute("SELECT temp_c FROM gpu_usage_samples")]
            self.assertEqual(heat, [("gpu", 61.0, 50.0)])
            self.assertEqual(gpu, [48, 48])
        leader.LAST_THERMAL.clear()


if __name__ == "__main__":
    unittest.main()
