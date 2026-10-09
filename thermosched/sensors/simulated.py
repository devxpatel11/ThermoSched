"""Deterministic guest-core thermal-state simulation."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

from thermosched.config import SchedulerConfig
from thermosched.models import CoreSample, CpuIdMap, ThermalKind, ThermalSnapshot
from thermosched.scheduler.heat import score_snapshot
from thermosched.sensors.base import SensorProvider


class SimulatedThermalSensor(SensorProvider):
    """Model per-guest-CPU thermal state from utilization and pacing inputs.

    Initial condition positions are model CPU IDs (0, 1, ...) and are mapped to
    the supplied eligible guest mask during construction. Runtime assignment IDs
    must already be guest IDs. The model uses no wall clock or live randomness.
    """

    def __init__(
        self,
        eligible_guest_cpus: Sequence[int],
        config: SchedulerConfig | None = None,
        *,
        seed: int | None = None,
        initial_temps_c: Sequence[float] | None = None,
    ) -> None:
        self.config = config or SchedulerConfig()
        cpus = tuple(eligible_guest_cpus)
        if not cpus or any(isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0 for cpu in cpus):
            raise ValueError("eligible_guest_cpus must contain non-negative guest CPU IDs")
        if len(cpus) != len(set(cpus)):
            raise ValueError("eligible_guest_cpus must be unique")
        self.cpu_id_map = CpuIdMap.from_eligible_guest_cpus(cpus)
        self._seed = self.config.simulation.seed if seed is None else seed
        if isinstance(self._seed, bool) or not isinstance(self._seed, int) or self._seed < 0:
            raise ValueError("seed must be a non-negative integer")
        self._initial_override = None if initial_temps_c is None else tuple(initial_temps_c)
        self._configured_initial_temps = self.config.simulation.initial_temps_c
        self._last_sampled_at_s: float | None = None
        self._temperatures: dict[int, float] = {}
        self._reset_state()

    @property
    def name(self) -> str:
        return "simulated-thermal-model-v1"

    @property
    def thermal_kind(self) -> ThermalKind:
        return ThermalKind.SIMULATED_C

    @property
    def seed(self) -> int:
        return self._seed

    def reset(self, *, seed: int | None = None) -> None:
        """Reset thermal state and time before a repeatable replay or run."""

        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
                raise ValueError("seed must be a non-negative integer")
            self._seed = seed
        self._last_sampled_at_s = None
        self._reset_state()

    def _reset_state(self) -> None:
        simulation = self.config.simulation
        explicit = self._initial_override
        if explicit is None:
            explicit = self._configured_initial_temps
        if len(explicit) > len(self.cpu_id_map.pairs):
            raise ValueError("initial temperatures exceed the eligible guest CPU count")
        for temperature in explicit:
            if (
                isinstance(temperature, bool)
                or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature)
                or not simulation.ambient_c <= temperature <= simulation.throttle_temp_c
            ):
                raise ValueError("initial temperatures must be finite and within configured bounds")

        if explicit:
            model_temps = tuple(explicit) + (simulation.ambient_c,) * (len(self.cpu_id_map.pairs) - len(explicit))
        elif simulation.initial_jitter_c:
            rng = random.Random(self._seed)
            model_temps = tuple(
                simulation.ambient_c + rng.uniform(0.0, simulation.initial_jitter_c)
                for _ in self.cpu_id_map.pairs
            )
        else:
            model_temps = (simulation.ambient_c,) * len(self.cpu_id_map.pairs)
        self._temperatures = {
            self.cpu_id_map.guest_cpu(model_cpu): temperature
            for model_cpu, temperature in enumerate(model_temps)
        }

    def sample(
        self,
        sampled_at_s: float,
        *,
        utilization_by_cpu: Mapping[int, float] | None = None,
        assigned_cpu: int | None = None,
        duty_cycle: float = 1.0,
        paused: bool = False,
    ) -> ThermalSnapshot:
        """Advance and return the simulated snapshot at a fixed timestamp.

        ``utilization_by_cpu`` is a per-guest-CPU percentage map. When omitted,
        an assigned workload is assumed to fully use its assigned CPU. Pacing
        scales only that workload's mapped CPU load; a pause removes its load.
        """

        if (
            isinstance(sampled_at_s, bool)
            or not isinstance(sampled_at_s, (int, float))
            or not math.isfinite(sampled_at_s)
            or sampled_at_s < 0
        ):
            raise ValueError("sampled_at_s must be finite and non-negative")
        if self._last_sampled_at_s is not None and sampled_at_s <= self._last_sampled_at_s:
            raise ValueError("sample timestamps must increase")
        if (
            isinstance(duty_cycle, bool)
            or not isinstance(duty_cycle, (int, float))
            or not math.isfinite(duty_cycle)
            or not 0.0 <= duty_cycle <= 1.0
        ):
            raise ValueError("duty_cycle must be between 0 and 1")
        eligible = self.cpu_id_map.eligible_guest_cpus
        if assigned_cpu is not None and assigned_cpu not in eligible:
            raise ValueError("assigned_cpu must be an eligible guest CPU ID")
        utilization = dict(utilization_by_cpu or {})
        if any(cpu not in eligible for cpu in utilization):
            raise ValueError("utilization contains a CPU outside the eligible guest mask")
        for value in utilization.values():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 <= value <= 100.0
            ):
                raise ValueError("per-CPU utilization must be between 0 and 100")
        if not isinstance(paused, bool):
            raise ValueError("paused must be a boolean")
        if utilization_by_cpu is not None and assigned_cpu is not None and assigned_cpu not in utilization:
            raise ValueError("assigned CPU is missing from the utilization map")

        previous_time = self._last_sampled_at_s
        elapsed_s = 0.0 if previous_time is None else sampled_at_s - previous_time
        self._last_sampled_at_s = sampled_at_s
        samples: list[CoreSample] = []
        simulation = self.config.simulation
        for cpu_id in eligible:
            load_pct = utilization.get(cpu_id, 0.0)
            if assigned_cpu is not None and cpu_id == assigned_cpu:
                if utilization_by_cpu is None:
                    load_pct = 100.0
                if paused:
                    load_pct = 0.0
                else:
                    load_pct *= duty_cycle
            temperature_before = self._temperatures[cpu_id]
            load_ratio = load_pct / 100.0
            delta = (
                simulation.heat_gain_per_s * load_ratio
                - simulation.cooling_per_s * (1.0 - load_ratio)
            ) * elapsed_s
            temperature_after = min(
                simulation.throttle_temp_c,
                max(simulation.ambient_c, temperature_before + delta),
            )
            self._temperatures[cpu_id] = temperature_after
            trend = 0.0 if elapsed_s == 0 else (temperature_after - temperature_before) / elapsed_s
            samples.append(
                CoreSample(
                    cpu_id=cpu_id,
                    utilization_pct=load_pct,
                    thermal_value=temperature_after,
                    thermal_kind=ThermalKind.SIMULATED_C,
                    trend_per_s=trend,
                )
            )

        snapshot = ThermalSnapshot(
            sampled_at_s,
            tuple(samples),
            f"simulated:{self.name}:seed={self._seed}",
        )
        return score_snapshot(snapshot, self.config)
