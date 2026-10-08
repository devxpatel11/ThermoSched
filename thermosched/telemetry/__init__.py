"""CPU and managed-process telemetry package."""

from thermosched.telemetry.cpu import (
    CpuTelemetryCollector,
    CpuUtilization,
    ExponentialMovingAverage,
    ManagedProcessTelemetry,
    TemperatureTrend,
    TemperatureTrendSample,
    TelemetrySnapshot,
)

__all__ = [
    "CpuTelemetryCollector",
    "CpuUtilization",
    "ExponentialMovingAverage",
    "ManagedProcessTelemetry",
    "TemperatureTrend",
    "TemperatureTrendSample",
    "TelemetrySnapshot",
]
