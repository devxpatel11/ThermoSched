"""Guest CPU utilization and explicitly managed child-process profiling."""

from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

import psutil

from thermosched.models import ProcessSample


@dataclass(frozen=True, slots=True)
class CpuUtilization:
    """One guest logical CPU's observational utilization."""

    cpu_id: int
    utilization_pct: float | None
    eligible: bool
    status: str

    def __post_init__(self) -> None:
        if self.cpu_id < 0:
            raise ValueError("cpu_id must be non-negative")
        if self.status not in {"priming", "available", "unavailable"}:
            raise ValueError("invalid CPU utilization status")
        if self.utilization_pct is not None and (
            not math.isfinite(self.utilization_pct) or self.utilization_pct < 0
        ):
            raise ValueError("utilization_pct must be finite and non-negative")
        if (self.status == "available") != (self.utilization_pct is not None):
            raise ValueError("available status requires a value; other statuses require None")


@dataclass(frozen=True, slots=True)
class ManagedProcessTelemetry:
    """Live and original process state, including explicit missing-value status."""

    pid: int
    cpu_percent: float | None
    current_affinity: tuple[int, ...] | None
    original_affinity: tuple[int, ...]
    current_cpu: int | None
    alive: bool | None
    status: str

    def __post_init__(self) -> None:
        if self.pid <= 1:
            raise ValueError("pid must be greater than 1")
        object.__setattr__(self, "original_affinity", tuple(self.original_affinity))
        if self.current_affinity is not None:
            object.__setattr__(self, "current_affinity", tuple(self.current_affinity))
        if self.cpu_percent is not None and (
            not math.isfinite(self.cpu_percent) or self.cpu_percent < 0
        ):
            raise ValueError("cpu_percent must be finite and non-negative")
        if any(cpu < 0 for cpu in self.original_affinity):
            raise ValueError("original affinity CPU IDs must be non-negative")
        if self.current_affinity is not None and any(cpu < 0 for cpu in self.current_affinity):
            raise ValueError("current affinity CPU IDs must be non-negative")
        if self.current_cpu is not None and self.current_cpu < 0:
            raise ValueError("current_cpu must be non-negative")
        if self.status not in {"priming", "available", "exited", "inaccessible", "identity_changed"}:
            raise ValueError("invalid managed process status")

    def as_process_sample(self) -> ProcessSample | None:
        """Return A1's controller input only when its utilization is valid."""

        if self.status != "available" or self.cpu_percent is None or self.current_affinity is None:
            return None
        return ProcessSample(
            self.pid,
            self.cpu_percent,
            self.current_affinity,
            self.current_cpu,
            bool(self.alive),
        )


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    """Single CPU/process snapshot for a controller sampling iteration."""

    sampled_at_s: float
    per_cpu: tuple[CpuUtilization, ...]
    eligible_guest_cpus: tuple[int, ...]
    process: ManagedProcessTelemetry | None
    process_cpu_intensity_pct: float | None
    cpu_status: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.sampled_at_s) or self.sampled_at_s < 0:
            raise ValueError("sampled_at_s must be finite and non-negative")
        object.__setattr__(self, "per_cpu", tuple(self.per_cpu))
        object.__setattr__(self, "eligible_guest_cpus", tuple(self.eligible_guest_cpus))
        if len({cpu.cpu_id for cpu in self.per_cpu}) != len(self.per_cpu):
            raise ValueError("per_cpu contains duplicate CPU IDs")
        if any(cpu < 0 for cpu in self.eligible_guest_cpus):
            raise ValueError("eligible guest CPU IDs must be non-negative")
        if self.cpu_status not in {"priming", "available", "unavailable"}:
            raise ValueError("invalid CPU telemetry status")
        if self.process_cpu_intensity_pct is not None and (
            not math.isfinite(self.process_cpu_intensity_pct) or self.process_cpu_intensity_pct < 0
        ):
            raise ValueError("process intensity must be finite and non-negative")


class ExponentialMovingAverage:
    """Generic EMA utility used for a process's rolling CPU intensity."""

    def __init__(self, alpha: float = 0.35) -> None:
        if not math.isfinite(alpha) or not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = alpha
        self.value: float | None = None

    def update(self, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("EMA input must be finite")
        self.value = value if self.value is None else self.alpha * value + (1 - self.alpha) * self.value
        return self.value


@dataclass(frozen=True, slots=True)
class TemperatureTrendSample:
    cpu_id: int
    sampled_at_s: float
    temperature_c: float
    trend_per_s: float | None


class TemperatureTrend:
    """Per-CPU timestamped temperature history and Celsius-per-second slope."""

    def __init__(self, max_samples: int = 8) -> None:
        if max_samples < 2:
            raise ValueError("max_samples must be at least 2")
        self._history: dict[int, deque[tuple[float, float]]] = {}
        self._max_samples = max_samples

    def add(self, cpu_id: int, sampled_at_s: float, temperature_c: float) -> TemperatureTrendSample:
        if cpu_id < 0:
            raise ValueError("cpu_id must be non-negative")
        if not math.isfinite(sampled_at_s) or sampled_at_s < 0:
            raise ValueError("sampled_at_s must be finite and non-negative")
        if not math.isfinite(temperature_c):
            raise ValueError("temperature_c must be finite")
        history = self._history.setdefault(cpu_id, deque(maxlen=self._max_samples))
        if history and sampled_at_s <= history[-1][0]:
            raise ValueError("temperature timestamps must increase for each CPU")
        previous = history[-1] if history else None
        history.append((sampled_at_s, temperature_c))
        slope = None if previous is None else (temperature_c - previous[1]) / (sampled_at_s - previous[0])
        return TemperatureTrendSample(cpu_id, sampled_at_s, temperature_c, slope)


class CpuTelemetryCollector:
    """Collect non-blocking CPU metrics and read-only state for a managed child.

    Only a live, same-user direct child of this process may be registered. Its
    creation time is checked on every sample to detect PID reuse. This collector
    never changes affinity or process run state.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        cpu_reader: Callable[[], Sequence[float]] | None = None,
        process_factory: Callable[[int], psutil.Process] = psutil.Process,
        intensity_alpha: float = 0.35,
    ) -> None:
        self._clock = clock
        self._cpu_reader = cpu_reader or (lambda: psutil.cpu_percent(interval=None, percpu=True))
        self._process_factory = process_factory
        self._cpu_primed = False
        self._registered_process: psutil.Process | None = None
        self._registered_create_time: float | None = None
        self._original_affinity: tuple[int, ...] = ()
        self._process_primed = False
        self._intensity = ExponentialMovingAverage(intensity_alpha)

    def register_child(self, pid: int) -> tuple[int, ...]:
        """Register an owned direct child and return its original CPU mask."""

        if pid <= 1 or pid == os.getpid():
            raise ValueError("PID is not an eligible managed child")
        process = self._process_factory(pid)
        try:
            if process.ppid() != os.getpid():
                raise ValueError("managed telemetry accepts only direct child processes")
            if hasattr(process, "uids") and process.uids().real != os.getuid():
                raise ValueError("managed child must be owned by the current user")
            created = process.create_time()
            affinity = tuple(sorted(process.cpu_affinity()))
            if not affinity:
                raise ValueError("managed child has an empty original CPU mask")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            raise ValueError(f"cannot register managed child {pid}: {exc.__class__.__name__}") from exc
        except (AttributeError, NotImplementedError) as exc:
            raise RuntimeError("process affinity telemetry is unavailable on this platform") from exc
        self._registered_process = process
        self._registered_create_time = created
        self._original_affinity = affinity
        self._process_primed = False
        self._intensity = ExponentialMovingAverage(self._intensity.alpha)
        return affinity

    def sample(self) -> TelemetrySnapshot:
        sampled_at = self._clock()
        eligible = self._original_affinity
        try:
            raw = tuple(self._cpu_reader())
            if self._cpu_primed:
                values: list[CpuUtilization] = []
                for cpu_id, value in enumerate(raw):
                    try:
                        percent = float(value)
                        if not math.isfinite(percent) or percent < 0:
                            raise ValueError("invalid utilization")
                        values.append(CpuUtilization(cpu_id, percent, cpu_id in eligible, "available"))
                    except (TypeError, ValueError):
                        values.append(CpuUtilization(cpu_id, None, cpu_id in eligible, "unavailable"))
                cpu_status = "available" if any(item.status == "available" for item in values) else "unavailable"
            else:
                values = [CpuUtilization(i, None, i in eligible, "priming") for i in range(len(raw))]
                cpu_status = "priming"
            self._cpu_primed = True
            per_cpu = tuple(values)
        except (psutil.Error, OSError, TypeError, ValueError):
            per_cpu = ()
            cpu_status = "unavailable"

        process, intensity = self._sample_process()
        return TelemetrySnapshot(sampled_at, per_cpu, eligible, process, intensity, cpu_status)

    def _sample_process(self) -> tuple[ManagedProcessTelemetry | None, float | None]:
        process = self._registered_process
        if process is None:
            return None, None
        try:
            if process.create_time() != self._registered_create_time:
                return self._process_state(None, None, None, None, "identity_changed"), None
            affinity = tuple(sorted(process.cpu_affinity()))
            try:
                current_cpu = process.cpu_num()
            except (psutil.Error, OSError, NotImplementedError):
                current_cpu = None
            utilization = float(process.cpu_percent(interval=None))
            if not math.isfinite(utilization) or utilization < 0:
                return self._process_state(None, affinity, current_cpu, True, "inaccessible"), None
            if not self._process_primed:
                self._process_primed = True
                return self._process_state(None, affinity, current_cpu, True, "priming"), None
            intensity = self._intensity.update(utilization)
            return self._process_state(utilization, affinity, current_cpu, True, "available"), intensity
        except psutil.NoSuchProcess:
            return self._process_state(None, None, None, False, "exited"), None
        except (psutil.AccessDenied, OSError):
            return self._process_state(None, None, None, None, "inaccessible"), None

    def _process_state(
        self,
        cpu_percent: float | None,
        affinity: tuple[int, ...] | None,
        current_cpu: int | None,
        alive: bool | None,
        status: str,
    ) -> ManagedProcessTelemetry:
        process = self._registered_process
        assert process is not None
        return ManagedProcessTelemetry(
            process.pid,
            cpu_percent,
            affinity,
            self._original_affinity,
            current_cpu,
            alive,
            status,
        )
