"""Dependency-injected controller loop for safe scheduling integration."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from types import FrameType
from typing import IO, Protocol

import psutil

from thermosched.config import SchedulerConfig
from thermosched.models import (
    CoreSample,
    CpuIdMap,
    DecisionAction,
    ManagedProcess,
    ProcessSample,
    ThermalSnapshot,
)
from thermosched.scheduler.actuator import Actuator
from thermosched.scheduler.policy import PolicyResult, PolicyState, evaluate_policy, record_migration
from thermosched.sensors.base import SensorProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelemetryFrame:
    process: ProcessSample
    per_cpu_utilization: tuple[float, ...]


class TelemetryProvider(Protocol):
    @property
    def name(self) -> str: ...

    def sample(self, target: ManagedProcess) -> TelemetryFrame: ...


class EventSink(Protocol):
    def emit(self, event: ControlEvent) -> None: ...


class Dashboard(Protocol):
    def render(self, event: ControlEvent, state: PidSchedulerState) -> None: ...


@dataclass(frozen=True, slots=True)
class ControlEvent:
    monotonic_s: float
    pid: int
    requested_action: str
    applied_action: str
    reason: str
    original_mask: tuple[int, ...]
    requested_mask: tuple[int, ...] | None
    observed_mask: tuple[int, ...] | None
    provenance: str
    sensor_backend: str
    telemetry_backend: str
    environment: str
    eligible_guest_cpus: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PidSchedulerState:
    last_core: int | None = None
    last_migration_time_s: float | None = None
    residency_s: float = 0.0
    consecutive_hot_count: int = 0
    paused: bool = False
    policy: PolicyState = PolicyState()


@dataclass(frozen=True, slots=True)
class RunSummary:
    pid: int
    iterations: int
    migrations: int
    paces: int
    failures: int
    stopped_by_signal: bool
    restoration_error: str | None = None


class PsutilTelemetryProvider:
    """Minimal real guest telemetry adapter replaceable by the future B2 backend."""

    @property
    def name(self) -> str:
        return "psutil:guest-vcpu"

    def sample(self, target: ManagedProcess) -> TelemetryFrame:
        try:
            process = psutil.Process(target.pid)
            if target.create_time_s is None or process.create_time() != target.create_time_s:
                raise RuntimeError("managed process identity changed")
            affinity = tuple(sorted(process.cpu_affinity()))
            current_cpu = affinity[0] if len(affinity) == 1 else process.cpu_num()
            sample = ProcessSample(
                pid=target.pid,
                cpu_percent=process.cpu_percent(interval=None),
                affinity=affinity,
                current_cpu=current_cpu,
                alive=process.is_running(),
            )
            return TelemetryFrame(sample, tuple(psutil.cpu_percent(interval=None, percpu=True)))
        except (psutil.Error, OSError, RuntimeError) as exc:
            logger.error("telemetry sample failed pid=%s: %s", target.pid, exc)
            raise RuntimeError(f"telemetry sample failed for PID {target.pid}: {exc}") from exc


class JsonLineSink:
    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def emit(self, event: ControlEvent) -> None:
        self._stream.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        self._stream.flush()


class ConsoleDashboard:
    def __init__(self, stream: IO[str] = sys.stdout) -> None:
        self._stream = stream

    def render(self, event: ControlEvent, state: PidSchedulerState) -> None:
        self._stream.write(
            f"pid={event.pid} request={event.requested_action} applied={event.applied_action} "
            f"reason={event.reason} provenance={event.provenance} "
            f"backend={event.sensor_backend}/{event.telemetry_backend} "
            f"guest_cpus={event.eligible_guest_cpus} paused={state.paused}\n"
        )
        self._stream.flush()


class Controller:
    def __init__(
        self,
        *,
        config: SchedulerConfig,
        sensor: SensorProvider,
        telemetry: TelemetryProvider,
        actuator: Actuator,
        event_sink: EventSink,
        dashboard: Dashboard,
        cpu_map: CpuIdMap,
        environment: str,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self.config = config
        self.sensor = sensor
        self.telemetry = telemetry
        self.actuator = actuator
        self.event_sink = event_sink
        self.dashboard = dashboard
        self.cpu_map = cpu_map
        self.environment = environment
        self.clock = clock
        self.wait = wait
        self.states: dict[int, PidSchedulerState] = {}
        self._stop = threading.Event()
        self._stopped_by_signal = False

    def request_stop(self) -> None:
        self._stop.set()

    @contextmanager
    def _signal_handlers(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

        def handle(signum: int, frame: FrameType | None) -> None:
            logger.info("controller received signal=%s; requesting safe shutdown", signum)
            self._stopped_by_signal = True
            self._stop.set()

        try:
            for signum in previous:
                signal.signal(signum, handle)
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def _map_snapshot(self, snapshot: ThermalSnapshot, telemetry: TelemetryFrame) -> ThermalSnapshot:
        mapped: list[CoreSample] = []
        for core in snapshot.cores:
            guest_cpu = self.cpu_map.guest_cpu(core.cpu_id)
            utilization = (
                telemetry.per_cpu_utilization[guest_cpu]
                if guest_cpu < len(telemetry.per_cpu_utilization)
                else core.utilization_pct
            )
            mapped.append(replace(core, cpu_id=guest_cpu, utilization_pct=utilization))
        return ThermalSnapshot(
            snapshot.sampled_at_s,
            tuple(mapped),
            f"{snapshot.source};model_to_guest={self.cpu_map.pairs}",
        )

    def _emit(self, event: ControlEvent, state: PidSchedulerState) -> None:
        try:
            self.event_sink.emit(event)
        except Exception as exc:
            logger.error("event sink failed; event preserved in fallback log: %s event=%s", exc, event)
        try:
            self.dashboard.render(event, state)
        except Exception as exc:
            logger.error("dashboard render failed; controller continuing safely: %s", exc)

    def _event(
        self,
        *,
        now_s: float,
        target: ManagedProcess,
        requested: str,
        applied: str,
        reason: str,
        provenance: str,
        requested_mask: tuple[int, ...] | None = None,
        observed_mask: tuple[int, ...] | None = None,
    ) -> ControlEvent:
        return ControlEvent(
            monotonic_s=now_s,
            pid=target.pid,
            requested_action=requested,
            applied_action=applied,
            reason=reason,
            original_mask=target.original_affinity,
            requested_mask=requested_mask,
            observed_mask=observed_mask,
            provenance=provenance,
            sensor_backend=self.sensor.name,
            telemetry_backend=self.telemetry.name,
            environment=self.environment,
            eligible_guest_cpus=target.eligible_guest_cpus,
        )

    def _wait(self, delay_s: float) -> bool:
        if delay_s <= 0:
            return self._stop.is_set()
        if self.wait is not None:
            return bool(self.wait(delay_s))
        return self._stop.wait(delay_s)

    def run(
        self,
        target: ManagedProcess,
        *,
        max_duration_s: float | None = None,
        max_iterations: int | None = None,
    ) -> RunSummary:
        if max_duration_s is not None and max_duration_s <= 0:
            raise ValueError("max_duration_s must be positive")
        state = PidSchedulerState()
        self.states[target.pid] = state
        started = self.clock()
        next_sample = started
        iterations = migrations = paces = failures = 0
        restoration_error: str | None = None
        self._stop.clear()
        self._stopped_by_signal = False

        try:
            with self._signal_handlers():
                while not self._stop.is_set():
                    now_s = self.clock()
                    if max_duration_s is not None and now_s - started >= max_duration_s:
                        break
                    if max_iterations is not None and iterations >= max_iterations:
                        break
                    provenance = "unavailable"
                    try:
                        telemetry = self.telemetry.sample(target)
                        if hasattr(self.sensor, "observe_assignment") and telemetry.process.current_cpu is not None:
                            model_cpu = self.cpu_map.model_cpu(telemetry.process.current_cpu)
                            duty_cycle = min(1.0, telemetry.process.cpu_percent / 100.0)
                            self.sensor.observe_assignment(model_cpu, duty_cycle)  # type: ignore[attr-defined]
                        snapshot = self._map_snapshot(self.sensor.sample(now_s), telemetry)
                        provenance = snapshot.source
                        result: PolicyResult = evaluate_policy(
                            snapshot,
                            telemetry.process,
                            state.policy,
                            self.config,
                            now_s=now_s,
                            eligible_guest_cpus=target.eligible_guest_cpus,
                        )
                    except Exception as exc:
                        failures += 1
                        logger.error("controller input degraded pid=%s: %s", target.pid, exc)
                        event = self._event(
                            now_s=now_s,
                            target=target,
                            requested="stay",
                            applied="skipped",
                            reason=f"component_failure:{type(exc).__name__}",
                            provenance=provenance,
                        )
                        self._emit(event, state)
                        iterations += 1
                        next_sample += self.config.sampling_interval_s
                        self._wait(max(0.0, next_sample - self.clock()))
                        continue

                    decision = result.decision
                    requested = decision.action.value
                    applied = "stay"
                    reason = decision.reason
                    requested_mask: tuple[int, ...] | None = None
                    observed_mask: tuple[int, ...] | None = None
                    policy_state = result.state
                    paused = False
                    try:
                        if decision.action is DecisionAction.MIGRATE:
                            assert decision.destination_cpu is not None
                            requested_mask = (decision.destination_cpu,)
                            observed_mask = self.actuator.set_affinity(target, requested_mask)
                            policy_state = record_migration(result.state, decision.destination_cpu, now_s)
                            applied = "migrate"
                            migrations += 1
                        elif decision.action is DecisionAction.PACE:
                            paused = True
                            self.states[target.pid] = replace(state, paused=True)
                            self.actuator.pace(target, decision.pace_ms, self.config.max_microbreak_ms)
                            paused = False
                            applied = "pace"
                            paces += 1
                    except Exception as exc:
                        paused = False
                        failures += 1
                        applied = "failed"
                        reason = f"actuator_failure:{exc}"
                        logger.error("requested action failed pid=%s action=%s: %s", target.pid, requested, exc)

                    observed_cpu = policy_state.observed_cpu
                    residency = max(0.0, now_s - policy_state.residency_started_s)
                    state = PidSchedulerState(
                        last_core=observed_cpu,
                        last_migration_time_s=policy_state.last_migration_s,
                        residency_s=residency,
                        consecutive_hot_count=policy_state.consecutive_hot_samples,
                        paused=paused,
                        policy=policy_state,
                    )
                    self.states[target.pid] = state
                    event = self._event(
                        now_s=now_s,
                        target=target,
                        requested=requested,
                        applied=applied,
                        reason=reason,
                        provenance=result.snapshot.source,
                        requested_mask=requested_mask,
                        observed_mask=observed_mask,
                    )
                    self._emit(event, state)
                    iterations += 1
                    next_sample += self.config.sampling_interval_s
                    if next_sample < self.clock():
                        next_sample = self.clock()
                    self._wait(max(0.0, next_sample - self.clock()))
        finally:
            try:
                self.actuator.restore(target)
            except Exception as exc:
                restoration_error = str(exc)
                failures += 1
                logger.error("final restoration failed pid=%s: %s", target.pid, exc)

        return RunSummary(
            target.pid,
            iterations,
            migrations,
            paces,
            failures,
            self._stopped_by_signal,
            restoration_error,
        )
