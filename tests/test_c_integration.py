from __future__ import annotations

import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from thermosched.cli import main
from thermosched.config import load_config
from thermosched.dashboard.terminal import TerminalDashboard
from thermosched.logging.logger import CSV_FIELDS, EventLogger
from thermosched.models import ThermalKind
from workloads import bursty, cpu_burn


def test_demo_configs_preserve_scenario_timing_and_pacing_contracts() -> None:
    migration = load_config("config/demo_migration.yaml")
    all_hot = load_config("config/demo_all_hot.yaml")

    assert migration.sampling_interval_s == 1.0
    assert migration.high_temp_c == 75.0
    assert all_hot.sampling_interval_s == 1.0
    assert all_hot.high_temp_c == 80.0
    assert all_hot.microbreak_ms == 200


def test_doctor_reports_runtime_scope_and_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(["doctor"])
    output = capsys.readouterr().out

    assert result in {0, 1}
    assert "Eligible guest CPU IDs:" in output
    assert "Thermal provenance:" in output
    assert "Actuator capability:" in output
    assert "Python:" in output


def test_baseline_launch_runs_only_its_new_child_and_logs_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Path("config/default.yaml").resolve()
    monkeypatch.chdir(tmp_path)

    result = main(
        [
            "launch",
            "--mode",
            "baseline",
            "--config",
            str(config),
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )

    assert result == 0
    record = json.loads(next((tmp_path / "logs").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert record["backend"] == "baseline-no-thermal-control"
    assert record["thermal_provenance"] == "none"
    assert record["requested_action"] == "launch"
    assert record["applied_action"] == "launched"
    assert "eligible_guest_cpus" in record


def test_launch_rejects_missing_workload(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["launch", "--mode", "baseline"]) == 2
    assert "no workload command" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "linux", reason="managed launch is a Linux userspace contract")
def test_sigterm_stops_only_the_launchers_managed_child(tmp_path: Path) -> None:
    config = Path("config/default.yaml").resolve()
    controller = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "thermosched",
            "launch",
            "--mode",
            "baseline",
            "--config",
            str(config),
            "--",
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child: psutil.Process | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child is None:
            children = psutil.Process(controller.pid).children()
            child = children[0] if children else None
            if child is None:
                time.sleep(0.02)
        assert child is not None

        os.kill(controller.pid, signal.SIGTERM)

        assert controller.wait(timeout=5) == 128 + signal.SIGTERM
        _, alive = psutil.wait_procs([child], timeout=5)
        assert alive == []
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)
        if child is not None and child.is_running():
            child.kill()


def test_event_logger_preserves_provenance_and_action_fields(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path, "review")
    logger.log_event(
        "DECISION",
        {
            "timestamp": 1.5,
            "thermal_provenance": "simulated_c",
            "backend": "simulate",
            "eligible_guest_cpus": [2, 7],
            "requested_action": "migrate",
            "applied_action": "stayed",
        },
    )

    with logger.csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert tuple(rows[0]) == CSV_FIELDS
    assert rows[0]["thermal_provenance"] == "simulated_c"
    assert rows[0]["requested_action"] == "migrate"
    assert rows[0]["applied_action"] == "stayed"
    assert json.loads(logger.jsonl_path.read_text(encoding="utf-8"))["eligible_guest_cpus"] == [2, 7]


def test_logger_rejects_run_id_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plain file name"):
        EventLogger(tmp_path, "../outside")


def test_dashboard_labels_celsius_and_risk_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    dashboard = TerminalDashboard(2)
    dashboard.render(
        [
            {"cpu_id": 2, "thermal_value": 70.0, "thermal_kind": ThermalKind.SIMULATED_C, "risk": 0.6},
            {"cpu_id": 7, "thermal_value": 0.4, "thermal_kind": ThermalKind.RISK_ONLY, "risk": 0.4},
        ]
    )
    output = capsys.readouterr().out

    assert "70.0 C [simulated_c]" in output
    assert "0.40 [risk_only]" in output
    assert "Guest CPU" in output


@pytest.mark.parametrize(
    ("workload", "arguments"),
    [
        (cpu_burn.main, ["--seconds", "0"]),
        (cpu_burn.main, ["--seconds", "inf"]),
        (bursty.main, ["--sleep-duration", "-1"]),
        (bursty.main, ["--burst-duration", "nan"]),
    ],
)
def test_workloads_reject_invalid_durations(workload: object, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        workload(arguments)  # type: ignore[operator]
    assert exc.value.code == 2
