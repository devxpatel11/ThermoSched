from __future__ import annotations

from pathlib import Path

from thermosched.sensors.linux_thermal import (
    discover_linux_thermal_sensors,
    format_sensor_inventory,
)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_discovers_thermal_zone_and_normalizes_milli_celsius(tmp_path: Path) -> None:
    thermal = tmp_path / "thermal"
    _write(thermal / "thermal_zone9" / "type", "x86_pkg_temp")
    _write(thermal / "thermal_zone9" / "temp", "67890")

    inventory = discover_linux_thermal_sensors(thermal, tmp_path / "hwmon")

    assert len(inventory.available) == 1
    sensor = inventory.available[0]
    assert sensor.temperature_c == 67.89
    assert sensor.sensor_name == "x86_pkg_temp"
    assert sensor.granularity == "package"
    assert sensor.source_path.endswith("thermal_zone9/temp")


def test_discovers_hwmon_core_label_without_assuming_chip_name(tmp_path: Path) -> None:
    hwmon = tmp_path / "hwmon"
    _write(hwmon / "hwmon4" / "name", "vendor_chip")
    _write(hwmon / "hwmon4" / "temp3_input", "51250")
    _write(hwmon / "hwmon4" / "temp3_label", "Core 3")

    inventory = discover_linux_thermal_sensors(tmp_path / "thermal", hwmon)

    sensor = inventory.available[0]
    assert sensor.sensor_name == "vendor_chip: Core 3"
    assert sensor.granularity == "core"
    assert sensor.temperature_c == 51.25


def test_ambiguous_cpu_sensor_stays_zone_scoped(tmp_path: Path) -> None:
    thermal = tmp_path / "thermal"
    _write(thermal / "thermal_zone0" / "type", "cpu-thermal")
    _write(thermal / "thermal_zone0" / "temp", "42000")

    inventory = discover_linux_thermal_sensors(thermal, tmp_path / "hwmon")

    assert inventory.available[0].granularity == "zone"


def test_missing_and_invalid_inputs_are_reported_without_fabricating_values(tmp_path: Path) -> None:
    thermal = tmp_path / "thermal"
    _write(thermal / "thermal_zone0" / "type", "mystery")
    _write(thermal / "thermal_zone0" / "temp", "not-a-number")
    (thermal / "thermal_zone1").mkdir(parents=True)

    inventory = discover_linux_thermal_sensors(thermal, tmp_path / "hwmon")

    assert [sensor.status for sensor in inventory.readings] == ["invalid", "unavailable"]
    assert all(sensor.temperature_c is None for sensor in inventory.readings)
    assert any("invalid thermal input" in message for message in inventory.diagnostics)
    assert any("unavailable thermal input" in message for message in inventory.diagnostics)


def test_no_sensor_result_and_doctor_output_are_clean(tmp_path: Path) -> None:
    inventory = discover_linux_thermal_sensors(tmp_path / "missing-thermal", tmp_path / "missing-hwmon")

    output = format_sensor_inventory(inventory)
    assert inventory.readings == ()
    assert "Thermal sensors: unavailable" in output
    assert "no thermal-zone or hwmon" in output


def test_doctor_output_lists_source_and_scope(tmp_path: Path) -> None:
    thermal = tmp_path / "thermal"
    _write(thermal / "thermal_zone1" / "type", "package")
    _write(thermal / "thermal_zone1" / "temp", "62000")

    output = format_sensor_inventory(discover_linux_thermal_sensors(thermal, tmp_path / "hwmon"))

    assert "scope=package" in output
    assert "source=" in output
    assert "62.0 C" in output
