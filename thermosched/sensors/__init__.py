"""Thermal sensor interfaces and backends."""

from thermosched.sensors.linux_thermal import (
    SensorInventory,
    SensorReading,
    discover_linux_thermal_sensors,
    format_sensor_inventory,
)
from thermosched.sensors.base import SensorProvider
from thermosched.sensors.backend import (
    ThermalBackendFrame,
    ThermalBackendSelection,
    ThermalSensorUnavailableError,
    select_thermal_backend,
)
from thermosched.sensors.simulated import SimulatedThermalSensor

__all__ = [
    "SensorInventory",
    "SensorReading",
    "SensorProvider",
    "SimulatedThermalSensor",
    "ThermalBackendFrame",
    "ThermalBackendSelection",
    "ThermalSensorUnavailableError",
    "discover_linux_thermal_sensors",
    "format_sensor_inventory",
    "select_thermal_backend",
]
