"""Explicit thermal simulation used until the B3 backend is integrated."""

from __future__ import annotations

from thermosched.config import SchedulerConfig
from thermosched.models import CoreSample, ThermalKind, ThermalSnapshot
from thermosched.sensors.base import SensorProvider


class SimulatedThermalProvider(SensorProvider):
    """Small assignment-coupled model; values are never presented as measured."""

    def __init__(self, cpu_count: int, scenario: str, config: SchedulerConfig) -> None:
        if cpu_count < 1:
            raise ValueError("simulation requires at least one model CPU")
        if scenario not in {"migration", "all-hot"}:
            raise ValueError(f"unknown simulation scenario: {scenario}")
        self._scenario = scenario
        self._config = config
        self._last_sample_s: float | None = None
        self._assigned_cpu = 0
        self._duty_cycle = 1.0
        if scenario == "migration":
            self._temperatures = [config.critical_temp_c + 2.0] + [config.simulation.ambient_c] * (
                cpu_count - 1
            )
        else:
            self._temperatures = [config.critical_temp_c + 2.0] * cpu_count
        self._trends = [0.0] * cpu_count

    @property
    def name(self) -> str:
        return f"simulation:assignment-coupled-v1:{self._scenario}"

    @property
    def thermal_kind(self) -> ThermalKind:
        return ThermalKind.SIMULATED_C

    def observe_assignment(self, model_cpu: int, duty_cycle: float) -> None:
        if not 0 <= model_cpu < len(self._temperatures):
            raise ValueError(f"model CPU {model_cpu} is outside the simulation")
        if not 0.0 <= duty_cycle <= 1.0:
            raise ValueError("duty_cycle must be between 0 and 1")
        self._assigned_cpu = model_cpu
        self._duty_cycle = duty_cycle

    def sample(self, sampled_at_s: float) -> ThermalSnapshot:
        elapsed = 0.0 if self._last_sample_s is None else max(0.0, sampled_at_s - self._last_sample_s)
        previous = tuple(self._temperatures)
        ambient = self._config.simulation.ambient_c
        for cpu_id, temperature in enumerate(previous):
            heat = (
                self._config.simulation.heat_gain_per_s * self._duty_cycle
                if cpu_id == self._assigned_cpu
                else 0.0
            )
            cooling = self._config.simulation.cooling_per_s * max(0.0, (temperature - ambient) / 20.0)
            if self._scenario == "all-hot":
                cooling = 0.0
            self._temperatures[cpu_id] = max(
                ambient,
                min(self._config.simulation.throttle_temp_c + 5.0, temperature + elapsed * (heat - cooling)),
            )
            self._trends[cpu_id] = (
                0.0 if elapsed == 0 else (self._temperatures[cpu_id] - temperature) / elapsed
            )
        self._last_sample_s = sampled_at_s
        cores = tuple(
            CoreSample(
                cpu_id=cpu_id,
                utilization_pct=(100.0 * self._duty_cycle if cpu_id == self._assigned_cpu else 0.0),
                thermal_value=temperature,
                thermal_kind=ThermalKind.SIMULATED_C,
                trend_per_s=self._trends[cpu_id],
            )
            for cpu_id, temperature in enumerate(self._temperatures)
        )
        return ThermalSnapshot(sampled_at_s, cores, self.name)
