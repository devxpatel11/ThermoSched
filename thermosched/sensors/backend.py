"""Thermal backend selection with measured-sensor provenance kept separate."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from thermosched.config import SchedulerConfig
from thermosched.models import CoreSample, ThermalKind, ThermalSnapshot
from thermosched.scheduler.heat import clamp, score_snapshot
from thermosched.sensors.base import SensorProvider
from thermosched.sensors.linux_thermal import (
    SensorInventory,
    SensorReading,
    discover_linux_thermal_sensors,
)
from thermosched.sensors.simulated import SimulatedThermalSensor


class ThermalSensorUnavailableError(RuntimeError):
    """Raised when real mode is requested without a usable measured source."""


@dataclass(frozen=True, slots=True)
class ThermalBackendFrame:
    """Policy core snapshot plus independent measured sensor evidence."""

    core_snapshot: ThermalSnapshot
    measured_readings: tuple[SensorReading, ...]
    backend: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "measured_readings", tuple(self.measured_readings))
        if not self.backend.strip():
            raise ValueError("backend name must not be empty")


class ThermalBackendSelection(SensorProvider):
    """A SensorProvider that retains non-core measured data alongside core risk."""

    def __init__(
        self,
        mode: str,
        eligible_guest_cpus: Sequence[int],
        config: SchedulerConfig,
        *,
        sensor_inventory: SensorInventory | None = None,
        seed: int | None = None,
    ) -> None:
        if mode not in {"simulate", "auto", "real"}:
            raise ValueError("mode must be simulate, auto, or real")
        cpus = tuple(eligible_guest_cpus)
        if (
            not cpus
            or len(cpus) != len(set(cpus))
            or any(isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0 for cpu in cpus)
        ):
            raise ValueError("eligible_guest_cpus must be a non-empty unique guest CPU mask")
        self.mode = mode
        self.eligible_guest_cpus = cpus
        self.config = config
        self._simulated: SimulatedThermalSensor | None = None
        self._measured: tuple[SensorReading, ...] = ()
        self._thermal_prior_c: float | None = None
        self.last_frame: ThermalBackendFrame | None = None

        if mode == "simulate":
            self._simulated = SimulatedThermalSensor(cpus, config, seed=seed)
            self._name = "simulate"
            return

        inventory = sensor_inventory if sensor_inventory is not None else discover_linux_thermal_sensors()
        usable = tuple(
            reading
            for reading in inventory.available
            if reading.granularity in {"package", "core", "zone"}
        )
        if usable:
            self._measured = usable
            # Core IDs cannot safely be inferred from physical-core or package
            # labels, so sensor values remain separate evidence. The hottest
            # useful source contributes a normalized thermal prior to derived
            # per-guest-CPU risk; no measured Celsius is copied into CoreSample.
            self._thermal_prior_c = max(reading.temperature_c for reading in usable if reading.temperature_c is not None)
            self._name = f"{mode}-sensor-derived-risk"
        elif mode == "real":
            raise ThermalSensorUnavailableError(
                "real mode requested but no readable package/core/zone thermal input is available"
            )
        else:
            self._simulated = SimulatedThermalSensor(cpus, config, seed=seed)
            self._name = "auto-simulate-fallback"

    @property
    def name(self) -> str:
        return self._name

    @property
    def thermal_kind(self) -> ThermalKind:
        return ThermalKind.SIMULATED_C if self._simulated is not None else ThermalKind.RISK_ONLY

    def sample_frame(
        self,
        sampled_at_s: float,
        *,
        utilization_by_cpu: Mapping[int, float] | None = None,
        assigned_cpu: int | None = None,
        duty_cycle: float = 1.0,
        paused: bool = False,
    ) -> ThermalBackendFrame:
        if self._simulated is not None:
            snapshot = self._simulated.sample(
                sampled_at_s,
                utilization_by_cpu=utilization_by_cpu,
                assigned_cpu=assigned_cpu,
                duty_cycle=duty_cycle,
                paused=paused,
            )
            frame = ThermalBackendFrame(snapshot, (), self._name)
        else:
            snapshot = self._derived_risk_snapshot(
                sampled_at_s,
                utilization_by_cpu=utilization_by_cpu,
                assigned_cpu=assigned_cpu,
                duty_cycle=duty_cycle,
                paused=paused,
            )
            frame = ThermalBackendFrame(snapshot, self._measured, self._name)
        self.last_frame = frame
        return frame

    def sample(self, sampled_at_s: float) -> ThermalSnapshot:
        """Implement SensorProvider for callers that only need policy inputs."""

        return self.sample_frame(sampled_at_s).core_snapshot

    def _derived_risk_snapshot(
        self,
        sampled_at_s: float,
        *,
        utilization_by_cpu: Mapping[int, float] | None,
        assigned_cpu: int | None,
        duty_cycle: float,
        paused: bool,
    ) -> ThermalSnapshot:
        if not math.isfinite(sampled_at_s) or sampled_at_s < 0:
            raise ValueError("sampled_at_s must be finite and non-negative")
        if not math.isfinite(duty_cycle) or not 0 <= duty_cycle <= 1:
            raise ValueError("duty_cycle must be between 0 and 1")
        utilization = dict(utilization_by_cpu or {})
        if any(cpu not in self.eligible_guest_cpus for cpu in utilization):
            raise ValueError("utilization contains a CPU outside the eligible guest mask")
        for value in utilization.values():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise ValueError("per-CPU utilization must be between 0 and 100")
        if assigned_cpu is not None and assigned_cpu not in self.eligible_guest_cpus:
            raise ValueError("assigned_cpu must be an eligible guest CPU ID")
        if utilization_by_cpu is not None and assigned_cpu is not None and assigned_cpu not in utilization:
            raise ValueError("assigned CPU is missing from the utilization map")
        assert self._thermal_prior_c is not None
        thermal_prior_risk = clamp(
            (self._thermal_prior_c - self.config.warm_temp_c)
            / (self.config.critical_temp_c - self.config.warm_temp_c)
        )
        cores: list[CoreSample] = []
        for cpu_id in self.eligible_guest_cpus:
            load = utilization.get(cpu_id, 0.0)
            if assigned_cpu == cpu_id:
                load = (100.0 if utilization_by_cpu is None else load) * (0.0 if paused else duty_cycle)
            cores.append(
                CoreSample(
                    cpu_id=cpu_id,
                    utilization_pct=load,
                    thermal_value=thermal_prior_risk,
                    thermal_kind=ThermalKind.RISK_ONLY,
                    trend_per_s=0.0,
                )
            )
        snapshot = ThermalSnapshot(
            sampled_at_s,
            tuple(cores),
            "derived-risk:measured-sensor-prior; measured values retained separately",
        )
        return score_snapshot(snapshot, self.config)


def select_thermal_backend(
    mode: str,
    eligible_guest_cpus: Sequence[int],
    config: SchedulerConfig | None = None,
    *,
    sensor_inventory: SensorInventory | None = None,
    seed: int | None = None,
) -> ThermalBackendSelection:
    """Select explicit simulation, sensor-informed auto mode, or real mode.

    Explicit ``simulate`` never probes or requires hardware sensors. ``auto``
    uses readable package/core/zone sources as measured evidence and derives
    core risk separately; if none are usable it announces simulation fallback.
    ``real`` raises a helpful error when no usable measured input exists.
    """

    return ThermalBackendSelection(
        mode,
        eligible_guest_cpus,
        config or SchedulerConfig(),
        sensor_inventory=sensor_inventory,
        seed=seed,
    )
