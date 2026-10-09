"""Dependency-injected controller loop joining the A, B, and C components."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from types import FrameType
from typing import Protocol

from thermosched.config import SchedulerConfig
from thermosched.dashboard.terminal import TerminalDashboard
from thermosched.logging.logger import EventLogger
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
from thermosched.sensors.backend import ThermalBackendSelection
from thermosched.sensors.base import SensorProvider
from thermosched.telemetry import CpuTelemetryCollector

logger = logging.getLogger(__name__)


class _TelemetryPending(RuntimeError):
    pass


class _ManagedChildExited(RuntimeError):
    pass


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
    current_cpu: int | None
    requested_action: str
    applied_action: str
    reason: str
    original_mask: tuple[int, ...]
    requested_mask: tuple[int, ...] | None
    observed_mask: tuple[int, ...] | None
    provenance: str
    thermal_provenance: str
    thermal_value: float | None
    risk_score: float | None
    pace_ms: int
    sensor_backend: str
    telemetry_backend: str
    environment: str
    eligible_guest_cpus: tuple[int, ...]
    model_to_guest: tuple[tuple[int, int], ...]
    measured_sources: tuple[str, ...] = ()
    cores: tuple[CoreSample, ...] = ()


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


class CpuTelemetryProvider:
    """Adapt B2 telemetry to the controller's managed-target interface."""

    name = "b2:cpu-telemetry"

    def __init__(self, collector: CpuTelemetryCollector) -> None:
        self.collector = collector

    def sample(self, target: ManagedProcess) -> TelemetryFrame:
        snapshot = self.collector.sample()
        process = snapshot.process
        if process is None:
            raise RuntimeError("B2 telemetry has no registered managed child")
        if process.pid != target.pid or process.original_affinity != target.original_affinity:
            raise RuntimeError("B2 telemetry target does not match the actuator registration")
        if process.status == "exited":
            raise _ManagedChildExited(f"managed child PID {target.pid} exited")
        if process.status == "priming":
            raise _TelemetryPending("B2 process telemetry is priming")
        sample = process.as_process_sample()
        if sample is None:
            raise RuntimeError(f"B2 process telemetry unavailable: {process.status}")
        if snapshot.cpu_status == "priming":
            raise _TelemetryPending("B2 per-CPU telemetry is priming")
        if snapshot.cpu_status != "available":
            raise RuntimeError("B2 per-CPU telemetry is unavailable")
        utilization = tuple(
            0.0 if cpu.utilization_pct is None else cpu.utilization_pct
            for cpu in snapshot.per_cpu
        )
        return TelemetryFrame(sample, utilization)


class EventLoggerSink:
    """Write A4 events through C2's CSV/JSONL logger."""

    def __init__(self, event_logger: EventLogger) -> None:
        self.logger = event_logger

    def emit(self, event: ControlEvent) -> None:
        data = asdict(event)
        data["timestamp"] = event.monotonic_s
        data["cpu_id"] = event.current_cpu
        if event.thermal_provenance == "measured_c":
            data["temp_measured"] = event.thermal_value
        elif event.thermal_provenance == "simulated_c":
            data["temp_simulated"] = event.thermal_value
        self.logger.log_event("CONTROL_DECISION", data)


class TerminalDashboardSink:
    """Render A4 snapshots through C2's provenance-aware terminal dashboard."""

    def __init__(self, dashboard: TerminalDashboard) -> None:
        self.dashboard = dashboard

    def render(self, event: ControlEvent, state: PidSchedulerState) -> None:
        status = f"{event.requested_action}->{event.applied_action}"
        stats = [
            {
                "cpu_id": core.cpu_id,
                "thermal_value": core.thermal_value,
                "thermal_kind": core.thermal_kind,
                "risk": core.risk,
                "status": status if core.cpu_id == event.current_cpu else "candidate",
            }
            for core in event.cores
        ]
        self.dashboard.render(
            stats,
            active_mode=event.sensor_backend,
            context={
                "environment": event.environment,
                "eligible_guest_cpus": event.eligible_guest_cpus,
                "model_to_guest": event.model_to_guest,
                "paused": state.paused,
            },
        )


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

        def handle(signum: int, _frame: FrameType | None) -> None:
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

    def _sample_thermal(
        self,
        now_s: float,
        telemetry: TelemetryFrame,
        target: ManagedProcess,
    ) -> tuple[ThermalSnapshot, tuple[str, ...]]:
        if isinstance(self.sensor, ThermalBackendSelection):
            utilization = {
                cpu: telemetry.per_cpu_utilization[cpu]
                for cpu in target.eligible_guest_cpus
                if cpu < len(telemetry.per_cpu_utilization)
            }
            frame = self.sensor.sample_frame(
                now_s,
                utilization_by_cpu=utilization,
                assigned_cpu=telemetry.process.current_cpu,
            )
            snapshot = frame.core_snapshot
            measured_sources = tuple(reading.source_path for reading in frame.measured_readings)
        else:
            snapshot = self.sensor.sample(now_s)
            measured_sources = ()
        snapshot_cpus = tuple(core.cpu_id for core in snapshot.cores)
        if not snapshot_cpus or not set(snapshot_cpus).issubset(target.eligible_guest_cpus):
            raise RuntimeError("thermal snapshot CPU IDs are outside the managed child's eligible guest mask")
        return snapshot, measured_sources

    def _emit(self, event: ControlEvent, state: PidSchedulerState) -> None:
        try:
            self.event_sink.emit(event)
        except Exception as exc:
            logger.error("event sink failed; controller continuing safely: %s event=%s", exc, event)
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
        current_cpu: int | None = None,
        snapshot: ThermalSnapshot | None = None,
        measured_sources: tuple[str, ...] = (),
        requested_mask: tuple[int, ...] | None = None,
        observed_mask: tuple[int, ...] | None = None,
        pace_ms: int = 0,
    ) -> ControlEvent:
        selected = snapshot.core(current_cpu) if snapshot is not None and current_cpu is not None else None
        if selected is None and snapshot is not None and snapshot.cores:
            selected = max(snapshot.cores, key=lambda core: core.risk)
        kinds = {core.thermal_kind.value for core in snapshot.cores} if snapshot is not None else set()
        thermal_provenance = next(iter(kinds)) if len(kinds) == 1 else ("mixed" if kinds else "unavailable")
        return ControlEvent(
            monotonic_s=now_s,
            pid=target.pid,
            current_cpu=current_cpu,
            requested_action=requested,
            applied_action=applied,
            reason=reason,
            original_mask=target.original_affinity,
            requested_mask=requested_mask,
            observed_mask=observed_mask,
            provenance="unavailable" if snapshot is None else snapshot.source,
            thermal_provenance=thermal_provenance,
            thermal_value=None if selected is None else selected.thermal_value,
            risk_score=None if selected is None else selected.risk,
            pace_ms=pace_ms,
            sensor_backend=self.sensor.name,
            telemetry_backend=self.telemetry.name,
            environment=self.environment,
            eligible_guest_cpus=target.eligible_guest_cpus,
            model_to_guest=self.cpu_map.pairs,
            measured_sources=measured_sources,
            cores=() if snapshot is None else snapshot.cores,
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
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if self.cpu_map.eligible_guest_cpus != target.eligible_guest_cpus:
            raise ValueError("controller CPU map must match the managed child's eligible guest mask")
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
                    try:
                        telemetry = self.telemetry.sample(target)
                        snapshot, measured_sources = self._sample_thermal(now_s, telemetry, target)
                        result: PolicyResult = evaluate_policy(
                            snapshot,
                            telemetry.process,
                            state.policy,
                            self.config,
                            now_s=now_s,
                            eligible_guest_cpus=target.eligible_guest_cpus,
                        )
                    except _ManagedChildExited:
                        logger.info("managed child exited pid=%s; stopping controller", target.pid)
                        break
                    except _TelemetryPending as exc:
                        event = self._event(
                            now_s=now_s,
                            target=target,
                            requested="stay",
                            applied="skipped",
                            reason=str(exc),
                        )
                        self._emit(event, state)
                        iterations += 1
                        next_sample += self.config.sampling_interval_s
                        self._wait(max(0.0, next_sample - self.clock()))
                        continue
                    except Exception as exc:
                        failures += 1
                        logger.error("controller input degraded pid=%s: %s", target.pid, exc)
                        event = self._event(
                            now_s=now_s,
                            target=target,
                            requested="stay",
                            applied="skipped",
                            reason=f"component_failure:{type(exc).__name__}",
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
                            self.states[target.pid] = PidSchedulerState(
                                last_core=state.last_core,
                                last_migration_time_s=state.last_migration_time_s,
                                residency_s=state.residency_s,
                                consecutive_hot_count=state.consecutive_hot_count,
                                paused=True,
                                policy=state.policy,
                            )
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

                    residency = max(0.0, now_s - policy_state.residency_started_s)
                    state = PidSchedulerState(
                        last_core=policy_state.observed_cpu,
                        last_migration_time_s=policy_state.last_migration_s,
                        residency_s=residency,
                        consecutive_hot_count=policy_state.consecutive_hot_samples,
                        paused=paused,
                        policy=policy_state,
                    )
                    self.states[target.pid] = state
                    event_cpu = (
                        observed_mask[0]
                        if applied == "migrate" and observed_mask is not None and len(observed_mask) == 1
                        else telemetry.process.current_cpu
                    )
                    event = self._event(
                        now_s=now_s,
                        target=target,
                        requested=requested,
                        applied=applied,
                        reason=reason,
                        current_cpu=event_cpu,
                        snapshot=result.snapshot,
                        measured_sources=measured_sources,
                        requested_mask=requested_mask,
                        observed_mask=observed_mask,
                        pace_ms=decision.pace_ms,
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
