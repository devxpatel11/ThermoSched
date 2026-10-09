from __future__ import annotations

from dataclasses import replace

import pytest

from thermosched.config import SchedulerConfig
from thermosched.controller import ControlEvent, Controller, PidSchedulerState, TelemetryFrame
from thermosched.models import (
    ActuatorCapabilities,
    CoreSample,
    CpuIdMap,
    ManagedProcess,
    ProcessSample,
    ThermalKind,
    ThermalSnapshot,
)
from thermosched.scheduler.actuator import ActuationError, Actuator
from thermosched.sensors import select_thermal_backend
from thermosched.sensors.base import SensorProvider


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.now += seconds
        return False


class FakeActuator(Actuator):
    def __init__(self, current_cpu: int, original: tuple[int, ...], *, fail: bool = False) -> None:
        self.current_cpu = current_cpu
        self.original = original
        self.fail = fail
        self.restored = False
        self.pace_count = 0

    def capabilities(self) -> ActuatorCapabilities:
        return ActuatorCapabilities(True, True, True)

    def get_affinity(self, target: ManagedProcess) -> tuple[int, ...]:
        return (self.current_cpu,)

    def set_affinity(self, target: ManagedProcess, cpu_ids: tuple[int, ...]) -> tuple[int, ...]:
        if self.fail:
            raise ActuationError("injected failure")
        self.current_cpu = cpu_ids[0]
        return cpu_ids

    def pause(self, target: ManagedProcess) -> None:
        pass

    def resume(self, target: ManagedProcess) -> None:
        pass

    def pace(self, target: ManagedProcess, duration_ms: int, max_duration_ms: int) -> float:
        if self.fail:
            raise ActuationError("injected failure")
        self.pace_count += 1
        return duration_ms / 1000.0

    def restore(self, target: ManagedProcess) -> None:
        self.restored = True


class FakeTelemetry:
    name = "fake:guest-telemetry"

    def __init__(self, actuator: FakeActuator, pid: int, cpu_count: int = 8) -> None:
        self.actuator = actuator
        self.pid = pid
        self.cpu_count = cpu_count

    def sample(self, target: ManagedProcess) -> TelemetryFrame:
        process = ProcessSample(
            self.pid,
            90.0,
            (self.actuator.current_cpu,),
            self.actuator.current_cpu,
            True,
        )
        return TelemetryFrame(process, tuple(90.0 for _ in range(self.cpu_count)))


class BrokenTelemetry:
    name = "broken"

    def sample(self, target: ManagedProcess) -> TelemetryFrame:
        raise RuntimeError("injected telemetry failure")


class CollectSink:
    def __init__(self) -> None:
        self.events: list[ControlEvent] = []

    def emit(self, event: ControlEvent) -> None:
        self.events.append(event)


class NullDashboard:
    def render(self, event: ControlEvent, state: PidSchedulerState) -> None:
        pass


class BrokenSink:
    def emit(self, event: ControlEvent) -> None:
        raise OSError("injected sink failure")


class BrokenDashboard:
    def render(self, event: ControlEvent, state: PidSchedulerState) -> None:
        raise RuntimeError("injected dashboard failure")


class FixedSensor:
    name = "simulation:fixed-clock-test"
    thermal_kind = ThermalKind.SIMULATED_C

    def __init__(self, temperatures: tuple[float, ...]) -> None:
        self.temperatures = temperatures

    def sample(self, sampled_at_s: float) -> ThermalSnapshot:
        return ThermalSnapshot(
            sampled_at_s,
            tuple(
                CoreSample(cpu, 90.0, temperature, ThermalKind.SIMULATED_C, 0.0)
                for cpu, temperature in zip((2, 7), self.temperatures, strict=True)
            ),
            self.name,
        )


def config(interval: float = 0.1) -> SchedulerConfig:
    return replace(
        SchedulerConfig(),
        sampling_interval_s=interval,
        confirmation_samples=1,
        min_residency_s=0.0,
        migration_cooldown_s=0.0,
        min_destination_improvement=0.01,
    )


def target() -> ManagedProcess:
    return ManagedProcess(9999, (2, 7), (2, 7))


def build_controller(
    sensor: SensorProvider,
    telemetry: FakeTelemetry | BrokenTelemetry,
    actuator: FakeActuator,
    sink: CollectSink,
    clock: FakeClock,
    interval: float = 0.1,
) -> Controller:
    return Controller(
        config=config(interval),
        sensor=sensor,
        telemetry=telemetry,
        actuator=actuator,
        event_sink=sink,
        dashboard=NullDashboard(),
        cpu_map=CpuIdMap(((0, 2), (1, 7))),
        environment="WSL2 test guest_cpus=(2, 7)",
        clock=clock,
        wait=clock.wait,
    )


def test_controller_applies_verified_guest_cpu_migration() -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7))
    sink = CollectSink()
    controller = build_controller(FixedSensor((84.0, 42.0)), FakeTelemetry(actuator, 9999), actuator, sink, clock)

    summary = controller.run(target(), max_iterations=1)

    assert summary.migrations == 1
    assert sink.events[0].requested_mask == (7,)
    assert sink.events[0].observed_mask == (7,)
    assert sink.events[0].applied_action == "migrate"
    assert sink.events[0].model_to_guest == ((0, 2), (1, 7))
    assert sink.events[0].thermal_provenance == "simulated_c"
    assert actuator.restored


def test_all_hot_requests_bounded_pacing_and_resumes_state() -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7))
    sink = CollectSink()
    controller = build_controller(FixedSensor((84.0, 84.0)), FakeTelemetry(actuator, 9999), actuator, sink, clock)

    summary = controller.run(target(), max_iterations=1)

    assert summary.paces == 1
    assert actuator.pace_count == 1
    assert sink.events[0].applied_action == "pace"
    assert controller.states[9999].paused is False


def test_telemetry_failure_degrades_to_skipped_control() -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7))
    sink = CollectSink()
    controller = build_controller(FixedSensor((84.0, 42.0)), BrokenTelemetry(), actuator, sink, clock)

    summary = controller.run(target(), max_iterations=1)

    assert summary.failures == 1
    assert sink.events[0].applied_action == "skipped"
    assert sink.events[0].reason == "component_failure:RuntimeError"
    assert actuator.current_cpu == 2
    assert actuator.restored


def test_actuator_failure_is_logged_without_false_migration_state() -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7), fail=True)
    sink = CollectSink()
    controller = build_controller(FixedSensor((84.0, 42.0)), FakeTelemetry(actuator, 9999), actuator, sink, clock)

    summary = controller.run(target(), max_iterations=1)

    assert summary.failures == 1
    assert summary.migrations == 0
    assert sink.events[0].requested_action == "migrate"
    assert sink.events[0].applied_action == "failed"
    assert controller.states[9999].last_migration_time_s is None


def test_output_failures_do_not_block_safe_control_or_restoration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7))
    controller = Controller(
        config=config(),
        sensor=FixedSensor((84.0, 42.0)),
        telemetry=FakeTelemetry(actuator, 9999),
        actuator=actuator,
        event_sink=BrokenSink(),
        dashboard=BrokenDashboard(),
        cpu_map=CpuIdMap(((0, 2), (1, 7))),
        environment="WSL2 test guest_cpus=(2, 7)",
        clock=clock,
        wait=clock.wait,
    )

    with caplog.at_level("ERROR"):
        summary = controller.run(target(), max_iterations=1)

    assert summary.migrations == 1
    assert summary.failures == 0
    assert actuator.restored
    assert "event sink failed" in caplog.text
    assert "dashboard render failed" in caplog.text


def test_fixed_clock_300_second_simulation_soak_is_separate_from_live_time() -> None:
    clock = FakeClock()
    actuator = FakeActuator(2, (2, 7))
    sink = CollectSink()
    sensor = select_thermal_backend("simulate", (2, 7), config(interval=0.5))
    controller = build_controller(sensor, FakeTelemetry(actuator, 9999), actuator, sink, clock, interval=0.5)

    summary = controller.run(target(), max_duration_s=300.0)

    assert clock.now == pytest.approx(300.0)
    assert summary.iterations == 600
    assert summary.failures == 0
    assert actuator.restored
    assert all("simulated:" in event.provenance for event in sink.events)
