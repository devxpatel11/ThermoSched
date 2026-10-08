"""Thermal sensor interfaces and backends."""

from thermosched.sensors.linux_thermal import (
    SensorInventory,
    SensorReading,
    discover_linux_thermal_sensors,
    format_sensor_inventory,
)
from thermosched.sensors.base import SensorProvider

__all__ = [
    "SensorInventory",
    "SensorReading",
    "SensorProvider",
    "discover_linux_thermal_sensors",
    "format_sensor_inventory",
]
