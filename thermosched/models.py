"""Stable, hardware-independent contracts shared by all ThermoSched modules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ThermalKind(str, Enum):
    """Provenance of a core's thermal input."""

    MEASURED_C = "measured_c"
    SIMULATED_C = "simulated_c"
    RISK_ONLY = "risk_only"


class DecisionAction(str, Enum):
    STAY = "stay"
    MIGRATE = "migrate"
    PACE = "pace"


def _finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class CoreSample:
    cpu_id: int
    utilization_pct: float
    thermal_value: float | None
    thermal_kind: ThermalKind
    trend_per_s: float
    risk: float = 0.0

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "thermal_kind", ThermalKind(self.thermal_kind))
        except ValueError as exc:
            raise ValueError(f"unknown thermal_kind: {self.thermal_kind}") from exc
        if self.cpu_id < 0:
            raise ValueError("cpu_id must be non-negative")
        _finite(self.utilization_pct, "utilization_pct")
        if not 0.0 <= self.utilization_pct <= 100.0:
            raise ValueError("utilization_pct must be between 0 and 100")
        if self.thermal_value is not None:
            _finite(self.thermal_value, "thermal_value")
        if self.thermal_kind is ThermalKind.RISK_ONLY:
            if self.thermal_value is not None and not 0.0 <= self.thermal_value <= 1.0:
                raise ValueError("risk-only thermal_value must be between 0 and 1")
        elif self.thermal_value is None:
            raise ValueError("measured and simulated samples require thermal_value")
        _finite(self.trend_per_s, "trend_per_s")
        _finite(self.risk, "risk")
        if not 0.0 <= self.risk <= 1.0:
            raise ValueError("risk must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ThermalSnapshot:
    sampled_at_s: float
    cores: tuple[CoreSample, ...]
    source: str

    def __post_init__(self) -> None:
        _finite(self.sampled_at_s, "sampled_at_s")
        if self.sampled_at_s < 0:
            raise ValueError("sampled_at_s must be non-negative")
        object.__setattr__(self, "cores", tuple(self.cores))
        cpu_ids = [core.cpu_id for core in self.cores]
        if len(cpu_ids) != len(set(cpu_ids)):
            raise ValueError("snapshot contains duplicate cpu_id values")
        if not self.source.strip():
            raise ValueError("snapshot source must describe its provenance")

    def core(self, cpu_id: int) -> CoreSample | None:
        return next((core for core in self.cores if core.cpu_id == cpu_id), None)


@dataclass(frozen=True, slots=True)
class ProcessSample:
    pid: int
    cpu_percent: float
    affinity: tuple[int, ...]
    current_cpu: int | None
    alive: bool

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("pid must be positive")
        _finite(self.cpu_percent, "cpu_percent")
        if self.cpu_percent < 0:
            raise ValueError("cpu_percent must be non-negative")
        object.__setattr__(self, "affinity", tuple(self.affinity))
        if any(cpu < 0 for cpu in self.affinity):
            raise ValueError("affinity CPU IDs must be non-negative")
        if len(self.affinity) != len(set(self.affinity)):
            raise ValueError("affinity CPU IDs must be unique")
        if self.current_cpu is not None and self.current_cpu < 0:
            raise ValueError("current_cpu must be non-negative")


@dataclass(frozen=True, slots=True)
class Decision:
    action: DecisionAction
    destination_cpu: int | None
    reason: str
    pace_ms: int = 0

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "action", DecisionAction(self.action))
        except ValueError as exc:
            raise ValueError(f"unknown decision action: {self.action}") from exc
        if not self.reason.strip():
            raise ValueError("decision reason must not be empty")
        if self.action is DecisionAction.MIGRATE:
            if self.destination_cpu is None or self.destination_cpu < 0:
                raise ValueError("migrate decisions require a valid destination_cpu")
            if self.pace_ms != 0:
                raise ValueError("migrate decisions cannot include pacing")
        elif self.action is DecisionAction.PACE:
            if self.destination_cpu is not None:
                raise ValueError("pace decisions cannot include a destination_cpu")
            if self.pace_ms <= 0:
                raise ValueError("pace decisions require a positive pace_ms")
        elif self.destination_cpu is not None or self.pace_ms != 0:
            raise ValueError("stay decisions cannot include actuation parameters")


@dataclass(frozen=True, slots=True)
class EnvironmentMetadata:
    kernel: str
    python_version: str
    psutil_version: str
    is_wsl: bool
    eligible_guest_cpus: tuple[int, ...]
    sensor_sources: tuple[str, ...] = ()
    windows_version: str | None = None
    wsl_distribution: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "eligible_guest_cpus", tuple(self.eligible_guest_cpus))
        object.__setattr__(self, "sensor_sources", tuple(self.sensor_sources))
        if not self.kernel or not self.python_version or not self.psutil_version:
            raise ValueError("kernel, Python, and psutil versions are required")
        if not self.eligible_guest_cpus:
            raise ValueError("at least one eligible guest CPU is required")
        if any(cpu < 0 for cpu in self.eligible_guest_cpus):
            raise ValueError("eligible guest CPU IDs must be non-negative")
        if len(self.eligible_guest_cpus) != len(set(self.eligible_guest_cpus)):
            raise ValueError("eligible guest CPU IDs must be unique")


@dataclass(frozen=True, slots=True)
class ActuatorCapabilities:
    affinity: bool
    pause_resume: bool
    restore: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))


@dataclass(frozen=True, slots=True)
class ManagedProcess:
    pid: int
    original_affinity: tuple[int, ...]
    eligible_guest_cpus: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.pid <= 1:
            raise ValueError("managed PID must be greater than 1")
        original = tuple(self.original_affinity)
        eligible = tuple(self.eligible_guest_cpus)
        object.__setattr__(self, "original_affinity", original)
        object.__setattr__(self, "eligible_guest_cpus", eligible)
        if not original or not eligible:
            raise ValueError("managed process affinity masks must not be empty")
        if any(cpu < 0 for cpu in original + eligible):
            raise ValueError("managed process CPU IDs must be non-negative")
        if len(original) != len(set(original)) or len(eligible) != len(set(eligible)):
            raise ValueError("managed process CPU IDs must be unique")
        if not set(eligible).issubset(original):
            raise ValueError("eligible guest CPUs must be within original affinity")


@dataclass(frozen=True, slots=True)
class CpuIdMap:
    """Maps deterministic model IDs onto actual eligible guest CPU IDs."""

    pairs: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pairs", tuple(self.pairs))
        model_ids = [pair[0] for pair in self.pairs]
        guest_ids = [pair[1] for pair in self.pairs]
        if not self.pairs:
            raise ValueError("CPU mapping must not be empty")
        if any(model < 0 or guest < 0 for model, guest in self.pairs):
            raise ValueError("CPU mapping IDs must be non-negative")
        if len(model_ids) != len(set(model_ids)) or len(guest_ids) != len(set(guest_ids)):
            raise ValueError("CPU mapping IDs must be one-to-one")

    @classmethod
    def from_eligible_guest_cpus(cls, cpu_ids: tuple[int, ...] | list[int]) -> CpuIdMap:
        guest_ids = tuple(cpu_ids)
        return cls(tuple(enumerate(guest_ids)))

    def guest_cpu(self, model_cpu: int) -> int:
        try:
            return dict(self.pairs)[model_cpu]
        except KeyError as exc:
            raise ValueError(f"model CPU {model_cpu} has no eligible guest mapping") from exc

    @property
    def eligible_guest_cpus(self) -> tuple[int, ...]:
        return tuple(guest for _, guest in self.pairs)


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    """One fixed-clock input frame for deterministic policy replay."""

    at_s: float
    thermal: ThermalSnapshot
    process: ProcessSample

    def __post_init__(self) -> None:
        _finite(self.at_s, "at_s")
        if self.at_s < 0:
            raise ValueError("at_s must be non-negative")
