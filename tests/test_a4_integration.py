from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from thermosched.config import load_config
from thermosched.controller import (
    Controller,
    CpuTelemetryProvider,
    EventLoggerSink,
    TerminalDashboardSink,
)
from thermosched.dashboard.terminal import TerminalDashboard
from thermosched.demo import _scenario_config
from thermosched.logging.logger import EventLogger
from thermosched.models import CpuIdMap
from thermosched.scheduler.actuator import LinuxActuator
from thermosched.sensors import select_thermal_backend
from thermosched.telemetry import CpuTelemetryCollector


pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux guest required")


@pytest.mark.parametrize(
    ("scenario", "config_name", "expected_action"),
    [
        ("migration", "demo_migration.yaml", "migrate"),
        ("all-hot", "demo_all_hot.yaml", "pace"),
    ],
)
def test_live_a_b_c_pipeline_controls_only_its_child_and_restores(
    scenario: str,
    config_name: str,
    expected_action: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workload = Path(__file__).parents[1] / "workloads" / "cpu_burn.py"
    child = subprocess.Popen([sys.executable, str(workload), "--seconds", "5"])
    actuator = LinuxActuator()
    try:
        target = actuator.register_child(child.pid)
        if len(target.eligible_guest_cpus) < 2:
            pytest.skip("live migration/pacing evidence requires at least two eligible guest CPUs")
        collector = CpuTelemetryCollector()
        assert collector.register_child(child.pid) == target.original_affinity
        telemetry = CpuTelemetryProvider(collector)
        config = replace(load_config(Path("config") / config_name), sampling_interval_s=0.05)
        config = _scenario_config(config, scenario, len(target.eligible_guest_cpus))
        sensor = select_thermal_backend("simulate", target.eligible_guest_cpus, config)
        cpu_map = CpuIdMap.from_eligible_guest_cpus(target.eligible_guest_cpus)
        actuator.set_affinity(target, (target.eligible_guest_cpus[0],))
        event_logger = EventLogger(tmp_path, scenario)
        controller = Controller(
            config=config,
            sensor=sensor,
            telemetry=telemetry,
            actuator=actuator,
            event_sink=EventLoggerSink(event_logger),
            dashboard=TerminalDashboardSink(TerminalDashboard(len(cpu_map.pairs))),
            cpu_map=cpu_map,
            environment=f"test-linux guest_cpu_scope={target.eligible_guest_cpus}",
        )

        summary = controller.run(target, max_duration_s=0.7)

        observed = actuator.get_affinity(target)
        events = [json.loads(line) for line in event_logger.jsonl_path.read_text(encoding="utf-8").splitlines()]
        applied = [event for event in events if event["applied_action"] == expected_action]
        assert summary.failures == 0
        assert applied
        if scenario == "all-hot":
            assert summary.migrations == 0
        assert observed == target.original_affinity
        assert all(event["pid"] == child.pid for event in events)
        assert all(tuple(event["eligible_guest_cpus"]) == target.eligible_guest_cpus for event in events)
        assert all(event["sensor_backend"] == "simulate" for event in applied)
        assert all(event["telemetry_backend"] == "b2:cpu-telemetry" for event in applied)
        assert all(event["thermal_provenance"] == "simulated_c" for event in applied)
        assert all(event["original_mask"] == list(target.original_affinity) for event in applied)
        if expected_action == "migrate":
            assert all(event["requested_mask"] == event["observed_mask"] for event in applied)
        else:
            assert all(event["pace_ms"] == 200 for event in applied)
        assert "eligible_guest_cpus=" in capsys.readouterr().out
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
            child.wait(timeout=5)
        assert not psutil.pid_exists(child.pid)


def test_controller_sigterm_restores_and_resumes_managed_child() -> None:
    helper_code = textwrap.dedent(
        """
        import json
        import os
        import signal
        import subprocess
        import sys
        from dataclasses import asdict, replace

        from thermosched.config import load_config
        from thermosched.controller import Controller, CpuTelemetryProvider
        from thermosched.models import CpuIdMap
        from thermosched.scheduler.actuator import LinuxActuator
        from thermosched.sensors import select_thermal_backend
        from thermosched.telemetry import CpuTelemetryCollector

        class ReadySink:
            ready = False
            def emit(self, event):
                if not self.ready:
                    self.ready = True
                    print('READY', flush=True)

        class NullDashboard:
            def render(self, event, state):
                pass

        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        actuator = LinuxActuator()
        target = actuator.register_child(child.pid)
        collector = CpuTelemetryCollector()
        collector.register_child(child.pid)
        config = replace(load_config('config/demo_migration.yaml'), sampling_interval_s=0.05)
        sensor = select_thermal_backend('simulate', target.eligible_guest_cpus, config)
        actuator.set_affinity(target, (target.eligible_guest_cpus[0],))
        print(json.dumps({'pid': child.pid, 'original': target.original_affinity}), flush=True)
        controller = Controller(
            config=config,
            sensor=sensor,
            telemetry=CpuTelemetryProvider(collector),
            actuator=actuator,
            event_sink=ReadySink(),
            dashboard=NullDashboard(),
            cpu_map=CpuIdMap.from_eligible_guest_cpus(target.eligible_guest_cpus),
            environment='signal-test',
        )
        summary = controller.run(target, max_duration_s=60)
        observed = actuator.get_affinity(target)
        print(json.dumps({'summary': asdict(summary), 'observed': observed}), flush=True)
        os.kill(child.pid, signal.SIGCONT)
        child.terminate()
        child.wait(timeout=5)
        """
    )
    helper = subprocess.Popen(
        [sys.executable, "-c", helper_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    setup: dict[str, object] | None = None
    try:
        assert helper.stdout is not None
        setup = json.loads(helper.stdout.readline())
        assert helper.stdout.readline().strip() == "READY"

        os.kill(helper.pid, signal.SIGTERM)

        result = json.loads(helper.stdout.readline())
        assert helper.wait(timeout=5) == 0, helper.stderr.read() if helper.stderr else ""
        assert result["summary"]["stopped_by_signal"] is True
        assert result["summary"]["restoration_error"] is None
        assert result["observed"] == setup["original"]
        assert not psutil.pid_exists(setup["pid"])
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)
        if setup is not None and psutil.pid_exists(int(setup["pid"])):
            workload = psutil.Process(int(setup["pid"]))
            workload.send_signal(signal.SIGCONT)
            workload.kill()
            workload.wait(timeout=5)
