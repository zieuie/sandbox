"""Machine temperatures for the dashboard's heat gauges: CPU package and NVMe, from Linux hwmon.

GPU temperatures come with the GPU usage sample (gpus.sample). Everything here is optional: a
machine without a sensor simply reports nothing for it, and the leader drops readings outside
a plausible range.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HWMON = Path("/sys/class/hwmon")
THERMAL = Path("/sys/class/thermal")
MIN_CELSIUS, MAX_CELSIUS = -20.0, 150.0


def read_celsius(path: Path) -> float | None:
    """A sysfs millidegree reading in degrees, or None when unreadable or implausible."""
    try:
        value = int(path.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None
    return value if MIN_CELSIUS <= value <= MAX_CELSIUS else None


def sensors(root: Path = HWMON) -> list[tuple[str, str, float]]:
    """Every (chip name, label, degrees) hwmon reports."""
    found = []
    for chip in sorted(root.glob("hwmon*")):
        try:
            name = (chip / "name").read_text().strip()
        except OSError:
            continue
        for reading in sorted(chip.glob("temp*_input")):
            celsius = read_celsius(reading)
            if celsius is None:
                continue
            try:
                label = reading.with_name(reading.name.replace("_input", "_label")).read_text().strip()
            except OSError:
                label = ""
            found.append((name, label, celsius))
    return found


def sample(root: Path = HWMON, thermal: Path = THERMAL) -> dict[str, float]:
    """{"cpu_c": hottest CPU package, "nvme_c": hottest NVMe drive}, each only when present.

    CPU: Intel coretemp "Package id N", else AMD k10temp "Tctl"/"Tdie", else the x86_pkg_temp
    thermal zone. NVMe: the drive's "Composite" temperature (its own overall reading).
    """
    readings = sensors(root)
    cpu = [c for name, label, c in readings if name == "coretemp" and label.startswith("Package id")]
    cpu = cpu or [c for name, label, c in readings if name == "k10temp" and label in ("Tctl", "Tdie")]
    if not cpu:
        for zone in sorted(thermal.glob("thermal_zone*")):
            try:
                if (zone / "type").read_text().strip() == "x86_pkg_temp":
                    celsius = read_celsius(zone / "temp")
                    if celsius is not None:
                        cpu.append(celsius)
            except OSError:
                continue
    nvme = [c for name, label, c in readings if name == "nvme" and label == "Composite"]
    result = {}
    if cpu:
        result["cpu_c"] = round(max(cpu), 1)
    if nvme:
        result["nvme_c"] = round(max(nvme), 1)
    return result


def normalized(raw: Any) -> dict[str, float]:
    """Validate a heartbeat's thermal sample; unknown keys and implausible values are dropped."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key in ("cpu_c", "nvme_c"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and MIN_CELSIUS <= value <= MAX_CELSIUS:
            result[key] = round(float(value), 1)
    return result
