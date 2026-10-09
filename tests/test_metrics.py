from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from thermosched.metrics import build_run_metadata, compare_runs, main, summarize_run
from thermosched.logging.logger import EventLogger
from workloads import cpu_burn


def _event(timestamp: float, temperature: float, action: str = "stay") -> dict[str, object]:
    return {
        "event_type": "CONTROL_DECISION",
        "monotonic_s": timestamp,
        "environment": "WSL2 kernel=test guest_cpu_scope=(2, 7)",
        "eligible_guest_cpus": [2, 7],
        "config_hash": "same-config",
        "high_temp_c": 75.0,
        "sampling_interval_s": 1.0,
        "workload": "cpu-burn",
        "workload_backend": "c1:cpu-burn",
        "seed": 4,
        "requested_action": action,
        "applied_action": action,
        "pace_ms": 100 if action == "pace" else 0,
        "requested_mask": [7] if action == "migrate" else None,
        "observed_mask": [7] if action == "migrate" else None,
        "cores": [
            {
                "cpu_id": 2,
                "utilization_pct": 20.0 + timestamp,
                "thermal_value": temperature,
                "thermal_kind": "simulated_c",
                "risk": 0.4,
            },
            {
                "cpu_id": 7,
                "utilization_pct": 40.0,
                "thermal_value": 88.0,
                "thermal_kind": "risk_only",
                "risk": 0.9,
            },
        ],
    }


def _write_log(path: Path, events: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


def test_summarize_separates_simulated_temperature_risk_and_action_metrics(tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "aware.jsonl",
        [
            _event(10.0, 80.0, "migrate"),
            _event(12.0, 70.0, "pace"),
            {"event_type": "WORKLOAD_PROGRESS", "monotonic_s": 12.0, "completed_units": 300, "work_unit": "iterations"},
            {"event_type": "WORKLOAD_PROGRESS", "monotonic_s": 10.0, "completed_units": 100, "work_unit": "iterations"},
            {"event_type": "RUN_CLEANUP", "cleanup_status": "restored", "controller_overhead_s": 0.125},
        ],
    )

    metrics = summarize_run(log)["metrics"]

    assert metrics["runtime_s"] == 2.0
    assert metrics["work_completed_units"] == 200.0
    assert metrics["throughput_units_per_s"] == 100.0
    assert metrics["peak_measured_per_core_c"] is None
    assert metrics["peak_simulated_c"] == 80.0
    assert metrics["peak_derived_risk"] == 0.9
    assert metrics["time_above_high_threshold_simulated_s"] == 2.0
    assert metrics["average_eligible_guest_cpu_utilization_pct"] == 35.5
    assert metrics["requested_decisions"] == {"stay": 0, "migrate": 1, "pace": 1}
    assert metrics["successful_affinity_readbacks"] == 1
    assert metrics["pacing_count"] == 1
    assert metrics["pacing_requested_time_s"] == 0.1
    assert metrics["cleanup_status"] == "restored"
    assert metrics["controller_overhead_s"] == 0.125


def test_compare_flags_incompatible_configs_and_retains_missing_values(tmp_path: Path) -> None:
    base = _write_log(tmp_path / "base.jsonl", [_event(1.0, 60.0), _event(2.0, 61.0)])
    aware_events = [_event(1.0, 62.0), _event(2.0, 63.0)]
    aware_events[0]["config_hash"] = "different-config"
    aware_events[1]["config_hash"] = "different-config"
    aware = _write_log(tmp_path / "aware.jsonl", aware_events)

    result = compare_runs(summarize_run(base), summarize_run(aware))

    assert result["compatible"] is False
    assert "config_hash differs" in result["mismatches"]
    assert result["delta_aware_minus_baseline"]["peak_measured_per_core_c"] is None


def test_metrics_cli_writes_json_and_strict_mode_flags_missing_compatibility(tmp_path: Path) -> None:
    baseline = _write_log(tmp_path / "baseline.jsonl", [_event(1.0, 60.0), _event(2.0, 61.0)])
    aware = _write_log(tmp_path / "aware.jsonl", [_event(1.0, 62.0), _event(2.0, 63.0)])
    output = tmp_path / "summary.json"

    assert main(["--baseline", str(baseline), "--aware", str(aware), "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["comparison"]["compatible"] is True
    assert main([
        "--baseline", str(baseline), "--aware", str(aware), "--require-compatible",
        "--high-threshold-c", "75", "--format", "csv", "--output", str(tmp_path / "summary.csv"),
    ]) == 0


def test_time_above_threshold_preserves_missing_sensor_data(tmp_path: Path) -> None:
    event1 = _event(1.0, 80.0)
    event2 = _event(2.0, 80.0)
    for event in (event1, event2):
        event["cores"] = [
            {"cpu_id": 2, "utilization_pct": 1.0, "thermal_value": None, "thermal_kind": "risk_only", "risk": 0.8}
        ]
    summary = summarize_run(_write_log(tmp_path / "risk.jsonl", [event1, event2]))

    assert summary["metrics"]["peak_measured_per_core_c"] is None
    assert summary["metrics"]["peak_simulated_c"] is None
    assert summary["metrics"]["time_above_high_threshold_simulated_s"] is None


def test_measured_core_temperature_never_combines_with_simulated_or_risk_values(tmp_path: Path) -> None:
    first = _event(1.0, 70.0)
    second = _event(2.0, 72.0)
    first["cores"].append(
        {"cpu_id": 9, "utilization_pct": 0.0, "thermal_value": 91.0, "thermal_kind": "measured_c", "risk": 0.5}
    )
    second["cores"].append(
        {"cpu_id": 9, "utilization_pct": 0.0, "thermal_value": 89.0, "thermal_kind": "measured_c", "risk": 0.4}
    )

    metrics = summarize_run(_write_log(tmp_path / "mixed.jsonl", [first, second]))["metrics"]

    assert metrics["peak_measured_per_core_c"] == 91.0
    assert metrics["peak_simulated_c"] == 72.0
    assert metrics["peak_derived_risk"] == 0.9


def test_run_metadata_records_provenance_and_leaves_user_supplied_versions_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("sampling_interval_s: 1\n", encoding="utf-8")
    config = SimpleNamespace(
        simulation=SimpleNamespace(seed=9),
        high_temp_c=75.0,
        sampling_interval_s=1.0,
    )

    metadata = build_run_metadata(
        config_path=config_path,
        config=config,
        eligible_guest_cpus=(7, 2, 7),
        backend="simulate",
        source_provenance="simulated_c",
        workload="cpu_burn",
    )

    assert len(metadata["config_hash"]) == 64
    assert metadata["eligible_mask"] == [2, 7]
    assert metadata["seed"] == 9
    assert metadata["backend"] == "simulate"
    assert metadata["source_provenance"] == "simulated_c"
    assert metadata["python_version"]
    assert metadata["psutil_version"]
    assert "windows_version" in metadata
    assert "wsl_version" in metadata


def test_csv_event_logger_input_preserves_available_metrics_and_missing_values(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path, "csv-run")
    logger.log_event(
        "CONTROL_DECISION",
        {
            "timestamp": 1.0,
            "thermal_provenance": "simulated_c",
            "temp_simulated": 78.0,
            "risk_score": 0.8,
            "requested_action": "migrate",
            "applied_action": "migrate",
            "requested_mask": (7,),
            "observed_mask": (7,),
            "pace_ms": 0,
        },
    )
    logger.log_event(
        "CONTROL_DECISION",
        {
            "timestamp": 2.0,
            "thermal_provenance": "simulated_c",
            "temp_simulated": 74.0,
            "risk_score": 0.7,
            "requested_action": "stay",
            "applied_action": "stay",
        },
    )

    metrics = summarize_run(logger.csv_path)["metrics"]

    assert metrics["runtime_s"] == 1.0
    assert metrics["peak_measured_per_core_c"] is None
    assert metrics["peak_simulated_c"] == 78.0
    assert metrics["successful_affinity_readbacks"] == 1
    assert metrics["throughput_units_per_s"] is None


def test_require_compatible_rejects_logs_without_environment_metadata(tmp_path: Path) -> None:
    baseline = _write_log(tmp_path / "base.jsonl", [_event(1.0, 60.0), _event(2.0, 61.0)])
    aware = _write_log(tmp_path / "aware.jsonl", [_event(1.0, 62.0), _event(2.0, 63.0)])
    for path in (baseline, aware):
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for record in records:
            for key in ("environment", "eligible_guest_cpus", "config_hash", "high_temp_c", "sampling_interval_s", "workload", "workload_backend", "seed"):
                record.pop(key, None)
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    assert main([
        "--baseline", str(baseline), "--aware", str(aware), "--require-compatible",
        "--output", str(tmp_path / "must-not-exist.json"),
    ]) == 2
    assert not (tmp_path / "must-not-exist.json").exists()


def test_cpu_burn_optional_progress_log_records_cumulative_work(tmp_path: Path) -> None:
    progress_path = tmp_path / "work.jsonl"

    assert cpu_burn.main(["--seconds", "0.01", "--progress-jsonl", str(progress_path)]) == 0
    records = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]

    assert records[0]["completed_units"] == 0
    assert records[-1]["completed_units"] > 0
    assert records[-1]["work_unit"] == "iterations"
    assert records[-1]["finished"] is True
