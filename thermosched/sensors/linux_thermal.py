"""Read-only discovery for Linux thermal-zone and hwmon temperature inputs."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from pathlib import Path


_CORE_RE = re.compile(r"(?:^|[^a-z0-9])core\s*[_-]?\s*(\d+)(?:$|[^0-9])", re.IGNORECASE)
_INPUT_RE = re.compile(r"temp(\d+)_input$")


@dataclass(frozen=True, slots=True)
class SensorReading:
    """One discovered input with explicit source, scope, and read status."""

    source_path: str
    sensor_name: str
    granularity: str
    temperature_c: float | None
    status: str

    def __post_init__(self) -> None:
        if self.granularity not in {"package", "core", "zone", "unknown"}:
            raise ValueError("granularity must be package, core, zone, or unknown")
        if self.status not in {"available", "unavailable", "invalid"}:
            raise ValueError("status must be available, unavailable, or invalid")
        if (self.status == "available") != (self.temperature_c is not None):
            raise ValueError("available readings require a temperature; other statuses require None")
        if self.temperature_c is not None and not math.isfinite(self.temperature_c):
            raise ValueError("temperature_c must be finite")


@dataclass(frozen=True, slots=True)
class SensorInventory:
    """Thermal source inventory; an empty tuple is a normal no-sensor result."""

    readings: tuple[SensorReading, ...]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "readings", tuple(self.readings))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def available(self) -> tuple[SensorReading, ...]:
        return tuple(reading for reading in self.readings if reading.status == "available")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _classify(name: str, path: Path) -> str:
    normalized = name.lower().replace("-", "_")
    if "package" in normalized or "pkg" in normalized:
        return "package"
    if _CORE_RE.search(normalized):
        return "core"
    # A clearly per-core hwmon label can say "Core 0"; ambiguous CPU names
    # remain zone-level rather than being promoted to a physical-core claim.
    if path.parts and "thermal_zone" in path.name:
        return "zone"
    return "unknown"


def _temperature(path: Path, *, milli_celsius: bool) -> tuple[float | None, str]:
    try:
        raw = _read_text(path)
    except OSError:
        return None, "unavailable"
    try:
        value = float(raw)
    except ValueError:
        return None, "invalid"
    if not math.isfinite(value):
        return None, "invalid"
    # Linux thermal-zone `temp` and hwmon `temp*_input` are specified in
    # millidegrees Celsius. Normalize only those semantic input files.
    return (value / 1000.0 if milli_celsius else value), "available"


def discover_linux_thermal_sensors(
    thermal_root: str | Path = "/sys/class/thermal",
    hwmon_root: str | Path = "/sys/class/hwmon",
) -> SensorInventory:
    """Discover readable thermal zones and hwmon inputs without assuming names.

    Paths are injectable so parser and failure behavior can be tested without
    host sensors. This function never infers per-core or Windows-host scope from
    an ambiguous source name.
    """

    readings: list[SensorReading] = []
    diagnostics: list[str] = []
    thermal_dir = Path(thermal_root)
    hwmon_dir = Path(hwmon_root)

    try:
        zones = sorted(thermal_dir.glob("thermal_zone*"))
    except OSError as exc:
        zones = []
        diagnostics.append(f"cannot list {thermal_dir}: {exc.__class__.__name__}")
    for zone in zones:
        temp_path = zone / "temp"
        try:
            name = _read_text(zone / "type")
        except OSError:
            name = zone.name
            diagnostics.append(f"sensor type unavailable: {zone / 'type'}")
        value, status = _temperature(temp_path, milli_celsius=True)
        if status != "available":
            diagnostics.append(f"{status} thermal input: {temp_path}")
        readings.append(
            SensorReading(str(temp_path), name, _classify(name, zone), value, status)
        )

    try:
        controllers = sorted(hwmon_dir.glob("hwmon*"))
    except OSError as exc:
        controllers = []
        diagnostics.append(f"cannot list {hwmon_dir}: {exc.__class__.__name__}")
    for controller in controllers:
        try:
            chip_name = _read_text(controller / "name")
        except OSError:
            chip_name = controller.name
            diagnostics.append(f"hwmon name unavailable: {controller / 'name'}")
        try:
            inputs = sorted(path for path in controller.glob("temp*_input") if _INPUT_RE.fullmatch(path.name))
        except OSError as exc:
            diagnostics.append(f"cannot list {controller}: {exc.__class__.__name__}")
            continue
        for temp_path in inputs:
            index_match = _INPUT_RE.fullmatch(temp_path.name)
            assert index_match is not None
            index = index_match.group(1)
            label_path = controller / f"temp{index}_label"
            try:
                label = _read_text(label_path)
            except OSError:
                label = ""
            display_name = f"{chip_name}: {label}" if label else f"{chip_name}: temp{index}"
            value, status = _temperature(temp_path, milli_celsius=True)
            if status != "available":
                diagnostics.append(f"{status} hwmon input: {temp_path}")
            readings.append(
                SensorReading(str(temp_path), display_name, _classify(display_name, temp_path), value, status)
            )

    if not readings:
        diagnostics.append("no thermal-zone or hwmon temperature inputs discovered")
    return SensorInventory(tuple(readings), tuple(diagnostics))


def format_sensor_inventory(inventory: SensorInventory) -> str:
    """Render the selected/readable sensor paths for doctor or debug output."""

    if not inventory.readings:
        lines = ["Thermal sensors: unavailable"]
    else:
        lines = ["Thermal sensor inventory:"]
        for reading in inventory.readings:
            value = "N/A" if reading.temperature_c is None else f"{reading.temperature_c:.1f} C"
            lines.append(
                f"- {reading.sensor_name}: {value} ({reading.status}; "
                f"scope={reading.granularity}; source={reading.source_path})"
            )
    lines.extend(f"- diagnostic: {message}" for message in inventory.diagnostics)
    return "\n".join(lines)
