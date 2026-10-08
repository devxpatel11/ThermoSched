from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from thermosched.telemetry.cpu import (
    CpuTelemetryCollector,
    ExponentialMovingAverage,
    TemperatureTrend,
)


class FakeProcess:
    def __init__(self, pid: int = 9001) -> None:
        self.pid = pid
        self.created = 1234.5
        self.affinity = [2, 7]
        self.cpu_values = iter([0.0, 175.0, 50.0])
        self.parent = __import__("os").getpid()
        self.uids_value = SimpleNamespace(real=__import__("os").getuid())
        self.fail_cpu_percent: Exception | None = None

    def ppid(self) -> int:
        return self.parent

    def uids(self) -> SimpleNamespace:
        return self.uids_value

    def create_time(self) -> float:
        return self.created

    def cpu_affinity(self) -> list[int]:
        return self.affinity

    def cpu_num(self) -> int:
        return 7

    def cpu_percent(self, interval: float | None = None) -> float:
        if self.fail_cpu_percent is not None:
            raise self.fail_cpu_percent
        return next(self.cpu_values)


def _collector(process: FakeProcess, cpu_reads: list[list[float]]) -> CpuTelemetryCollector:
    return CpuTelemetryCollector(
        clock=iter([1.0, 2.0, 3.0, 4.0]).__next__,
        cpu_reader=lambda: cpu_reads.pop(0),
        process_factory=lambda _pid: process,  # type: ignore[arg-type]
    )


def test_first_nonblocking_samples_are_marked_priming_not_zero_load() -> None:
    process = FakeProcess()
    collector = _collector(process, [[0.0] * 8, [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]])
    assert collector.register_child(process.pid) == (2, 7)

    first = collector.sample()
    assert first.cpu_status == "priming"
    assert all(item.status == "priming" and item.utilization_pct is None for item in first.per_cpu)
    assert first.process is not None and first.process.status == "priming"
    assert first.process.as_process_sample() is None

    second = collector.sample()
    assert second.cpu_status == "available"
    assert second.eligible_guest_cpus == (2, 7)
    assert second.per_cpu[2].utilization_pct == 12.0
    assert second.per_cpu[2].eligible
    assert not second.per_cpu[3].eligible
    assert second.process is not None
    assert second.process.status == "available"
    assert second.process.cpu_percent == 175.0
    assert second.process.as_process_sample().cpu_percent == 175.0  # type: ignore[union-attr]


def test_pid_reuse_is_reported_and_not_sampled_as_original_child() -> None:
    process = FakeProcess()
    collector = _collector(process, [[5.0] * 8, [5.0] * 8])
    collector.register_child(process.pid)
    collector.sample()
    process.created += 1

    snapshot = collector.sample()

    assert snapshot.process is not None
    assert snapshot.process.status == "identity_changed"
    assert snapshot.process_cpu_intensity_pct is None


def test_disappearing_process_is_reported_cleanly() -> None:
    process = FakeProcess()
    collector = _collector(process, [[1.0] * 8, [2.0] * 8])
    collector.register_child(process.pid)
    collector.sample()
    process.fail_cpu_percent = psutil.NoSuchProcess(process.pid)

    snapshot = collector.sample()

    assert snapshot.process is not None
    assert snapshot.process.status == "exited"
    assert snapshot.process.alive is False
    assert snapshot.process.as_process_sample() is None


def test_access_denied_is_annotated_instead_of_crashing() -> None:
    process = FakeProcess()
    collector = _collector(process, [[1.0] * 8, [2.0] * 8])
    collector.register_child(process.pid)
    collector.sample()
    process.fail_cpu_percent = psutil.AccessDenied(process.pid)

    snapshot = collector.sample()

    assert snapshot.process is not None
    assert snapshot.process.status == "inaccessible"
    assert snapshot.process.cpu_percent is None


def test_registration_rejects_non_child_process() -> None:
    process = FakeProcess()
    process.parent += 1
    collector = _collector(process, [])

    with pytest.raises(ValueError, match="direct child"):
        collector.register_child(process.pid)


def test_registration_reports_a_process_that_disappears_during_lookup() -> None:
    def missing(pid: int) -> FakeProcess:
        raise psutil.NoSuchProcess(pid)

    collector = CpuTelemetryCollector(process_factory=missing)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="NoSuchProcess"):
        collector.register_child(9001)


def test_invalid_process_utilization_degrades_to_inaccessible() -> None:
    process = FakeProcess()
    process.cpu_values = iter(["invalid"])  # type: ignore[list-item]
    collector = _collector(process, [[1.0] * 8])
    collector.register_child(process.pid)

    snapshot = collector.sample()

    assert snapshot.process is not None
    assert snapshot.process.status == "inaccessible"
    assert snapshot.process.as_process_sample() is None


def test_out_of_range_per_cpu_utilization_is_unavailable() -> None:
    process = FakeProcess()
    collector = _collector(
        process,
        [[0.0] * 8, [101.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0]],
    )
    collector.register_child(process.pid)
    collector.sample()

    snapshot = collector.sample()

    assert snapshot.per_cpu[0].status == "unavailable"
    assert snapshot.per_cpu[0].utilization_pct is None
    assert snapshot.cpu_status == "available"


def test_cpu_reader_failure_is_an_unavailable_snapshot() -> None:
    collector = CpuTelemetryCollector(cpu_reader=lambda: (_ for _ in ()).throw(psutil.AccessDenied()))

    snapshot = collector.sample()

    assert snapshot.cpu_status == "unavailable"
    assert snapshot.per_cpu == ()


def test_ema_tracks_unclamped_multithreaded_cpu_percent() -> None:
    ema = ExponentialMovingAverage(alpha=0.5)

    assert ema.update(180.0) == 180.0
    assert ema.update(100.0) == 140.0


def test_temperature_trend_is_timestamped_and_per_cpu() -> None:
    trend = TemperatureTrend()

    first = trend.add(2, 10.0, 60.0)
    second = trend.add(2, 12.0, 63.0)
    other_cpu = trend.add(7, 12.0, 50.0)

    assert first.trend_per_s is None
    assert second.trend_per_s == 1.5
    assert other_cpu.trend_per_s is None
    with pytest.raises(ValueError, match="timestamps must increase"):
        trend.add(2, 12.0, 64.0)
