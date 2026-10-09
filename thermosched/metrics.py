"""Parse ThermoSched event logs into provenance-aware run comparisons."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import logging
import math
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_COMPATIBILITY_FIELDS = (
    "environment",
    "eligible_guest_cpus",
    "config_hash",
    "high_temp_c",
    "sampling_interval_s",
    "workload",
    "workload_backend",
    "seed",
)


def build_run_metadata(
    *,
    config_path: str | Path,
    config: Any,
    eligible_guest_cpus: Iterable[int],
    backend: str,
    source_provenance: str,
    workload: str,
) -> dict[str, Any]:
    """Capture run provenance without guessing host versions or sensor scope."""

    config_file = Path(config_path)
    try:
        config_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning("could not hash run config %s: %s", config_file, exc)
        config_hash = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=config_file.resolve().parent.parent,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("could not capture repository commit for run metadata: %s", exc)
        commit = None
    distro: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                distro[key] = value.strip().strip('"')
    except OSError:
        logger.info("Linux distribution metadata is unavailable")
    simulation = getattr(config, "simulation", None)
    eligible = sorted(set(int(cpu) for cpu in eligible_guest_cpus))
    environment = "WSL2" if "microsoft" in platform.release().lower() else platform.system()
    return {
        "environment": environment,
        "windows_version": os.environ.get("THERMOSCHED_WINDOWS_VERSION"),
        "wsl_version": os.environ.get("THERMOSCHED_WSL_VERSION"),
        "ubuntu_version": distro.get("PRETTY_NAME"),
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "psutil_version": _psutil_version(),
        "eligible_guest_cpus": eligible,
        "eligible_mask": eligible,
        "config_hash": config_hash,
        "high_temp_c": getattr(config, "high_temp_c", None),
        "sampling_interval_s": getattr(config, "sampling_interval_s", None),
        "seed": getattr(simulation, "seed", None),
        "backend": backend,
        "source_provenance": source_provenance,
        "thermal_provenance": source_provenance,
        "workload": workload,
        "workload_backend": "c1:cpu-burn" if workload == "cpu_burn" else workload,
        "commit": commit,
    }


def _psutil_version() -> str | None:
    try:
        import psutil

        return psutil.__version__
    except ImportError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        if stripped[:1] in {"[", "(", "{"}:
            try:
                return ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                pass
        return value


def _canonical(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _canonical(item)) for key, item in value.items()))
    return value


def load_events(path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL or CSV events, raising a useful error for malformed input."""

    source = Path(path)
    try:
        content = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read event log {source}: {exc}") from exc
    if not content.strip():
        raise ValueError(f"event log is empty: {source}")

    events: list[dict[str, Any]] = []
    if source.suffix.lower() == ".csv":
        try:
            events = [
                {key: _decode(value) for key, value in row.items() if key is not None}
                for row in csv.DictReader(content.splitlines())
            ]
        except csv.Error as exc:
            raise ValueError(f"invalid CSV event log {source}: {exc}") from exc
    else:
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {source} line {line_number}: {exc.msg}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"event log {source} line {line_number} must contain a JSON object")
            events.append(event)
    if not events:
        raise ValueError(f"event log contains no events: {source}")
    return events


def _metadata(events: list[dict[str, Any]]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for event in events:
        if event.get("event_type") == "RUN_METADATA":
            nested = event.get("metadata")
            if isinstance(nested, dict):
                metadata.update(nested)
            metadata.update({key: value for key, value in event.items() if key not in {"metadata", "event_type"}})
        for key in (
            "environment", "eligible_guest_cpus", "config_hash", "high_temp_c",
            "sampling_interval_s", "workload", "workload_backend", "seed",
            "backend", "sensor_backend", "thermal_provenance", "source_provenance",
            "commit", "windows_version", "wsl_version", "ubuntu_version", "kernel",
            "python_version", "psutil_version", "config", "cleanup_status", "restored",
        ):
            if key in event and event[key] not in (None, ""):
                metadata.setdefault(key, event[key])
    return metadata


def _event_time(event: dict[str, Any]) -> float | None:
    value = _number(event.get("monotonic_s"))
    if value is not None:
        return value
    if event.get("event_type") in {"CONTROL_DECISION", "WORKLOAD_PROGRESS", "WORKLOAD_COMPLETE", "RUN_END"}:
        return _number(event.get("timestamp"))
    return None


def _samples(events: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any], dict[str, Any] | None]]:
    result = []
    for event in events:
        timestamp = _event_time(event)
        if timestamp is None:
            continue
        cores = event.get("cores")
        if isinstance(cores, list) and cores:
            for core in cores:
                if isinstance(core, dict):
                    result.append((timestamp, event, core))
        else:
            result.append((timestamp, event, None))
    return sorted(result, key=lambda item: item[0])


def _peak_temperature(samples: list[tuple[float, dict[str, Any], dict[str, Any] | None]], kind: str) -> float | None:
    values: list[float] = []
    for _timestamp, event, core in samples:
        provenance = (core or {}).get("thermal_kind", event.get("thermal_provenance"))
        if provenance != kind:
            continue
        value = (core or {}).get("thermal_value")
        if value is None:
            value = event.get("temp_measured" if kind == "measured_c" else "temp_simulated")
        numeric = _number(value)
        if numeric is not None:
            values.append(numeric)
    return max(values) if values else None


def _time_above(
    samples: list[tuple[float, dict[str, Any], dict[str, Any] | None]],
    threshold: float | None,
    kind: str,
) -> float | None:
    if threshold is None:
        return None
    hottest_by_timestamp: dict[float, float] = {}
    for timestamp, event, core in samples:
        provenance = (core or {}).get("thermal_kind", event.get("thermal_provenance"))
        if provenance != kind:
            continue
        value = (core or {}).get("thermal_value")
        if value is None:
            value = event.get("temp_measured" if kind == "measured_c" else "temp_simulated")
        numeric = _number(value)
        if numeric is None:
            continue
        hottest_by_timestamp[timestamp] = max(hottest_by_timestamp.get(timestamp, numeric), numeric)
    if not hottest_by_timestamp:
        return None
    points = sorted(hottest_by_timestamp.items())
    return sum(
        end - start
        for (start, value), (end, _next_value) in zip(points, points[1:])
        if end > start and value > threshold
    )


def summarize_run(
    path: str | Path,
    *,
    label: str | None = None,
    high_threshold_c: float | None = None,
) -> dict[str, Any]:
    """Aggregate one event log; unavailable inputs remain JSON null."""

    events = load_events(path)
    metadata = _metadata(events)
    threshold = high_threshold_c
    if threshold is None:
        threshold = _number(metadata.get("high_temp_c", metadata.get("high_threshold_c")))
    if threshold is not None and not math.isfinite(threshold):
        raise ValueError("high-temperature threshold must be finite")
    samples = _samples(events)
    timestamps = sorted({timestamp for timestamp, _event, _core in samples})
    runtime = timestamps[-1] - timestamps[0] if len(timestamps) >= 2 else None

    cpu_values: list[float] = []
    risk_values: list[float] = []
    progress: list[tuple[float, float, str | None]] = []
    controller_overhead: list[float] = []
    for timestamp, event, core in samples:
        utilization = _number((core or {}).get("utilization_pct"))
        if utilization is None and core is None:
            utilization = _number(event.get("cpu_utilization_pct"))
        if utilization is not None:
            cpu_values.append(utilization)
        risk = _number((core or {}).get("risk", event.get("risk_score")))
        if risk is not None:
            risk_values.append(risk)

    for event in events:
        overhead = _number(event.get("controller_overhead_s"))
        if overhead is not None and overhead >= 0:
            controller_overhead.append(overhead)
        timestamp = _event_time(event)
        if timestamp is None:
            continue
        for field in ("completed_units", "progress_units", "work_completed_units"):
            units = _number(event.get(field))
            if units is not None:
                progress.append((timestamp, units, event.get("work_unit")))
                break

    progress_values = [value for _time, value, _unit in progress]
    units_done = max(progress_values) - min(progress_values) if len(progress_values) >= 2 else None
    if units_done is None and _number(metadata.get("work_completed_units")) is not None:
        units_done = _number(metadata.get("work_completed_units"))
    unit_names = {unit for _time, _value, unit in progress if unit}
    unit_name = next(iter(unit_names)) if len(unit_names) == 1 else metadata.get("work_unit")
    throughput = units_done / runtime if units_done is not None and runtime and runtime > 0 else None

    requested = {action: 0 for action in ("stay", "migrate", "pace")}
    applied: dict[str, int] = {
        action: 0 for action in ("stay", "migrate", "pace", "skipped", "failed")
    }
    successful_readbacks = 0
    failed_actions = 0
    pace_ms = 0.0
    cleanup: Any = metadata.get("cleanup_status")
    if cleanup is None and "restored" in metadata:
        cleanup = "restored" if metadata["restored"] else "failed"
    for event in events:
        request = event.get("requested_action")
        action = event.get("applied_action")
        if request in requested:
            requested[request] += 1
        if action in applied:
            applied[action] += 1
        if action == "failed" or event.get("event_type") in {"ACTION_FAILED", "ACTUATOR_FAILURE"}:
            failed_actions += 1
        if action == "migrate":
            asked = _decode(event.get("requested_mask"))
            observed = _decode(event.get("observed_mask"))
            if isinstance(asked, (list, tuple)) and isinstance(observed, (list, tuple)) and list(asked) == list(observed):
                successful_readbacks += 1
        if action == "pace":
            requested_ms = _number(event.get("pace_ms"))
            if requested_ms is not None and requested_ms >= 0:
                pace_ms += requested_ms
        if event.get("event_type") == "RUN_CLEANUP":
            cleanup = event.get("cleanup_status", event.get("restored", "unknown"))

    average_cpu = sum(cpu_values) / len(cpu_values) if cpu_values else None
    result: dict[str, Any] = {
        "label": label or Path(path).stem,
        "source": str(path),
        "metadata": metadata,
        "metrics": {
            "runtime_s": runtime,
            "work_completed_units": units_done,
            "work_unit": unit_name,
            "throughput_units_per_s": throughput,
            "peak_measured_per_core_c": _peak_temperature(samples, "measured_c"),
            "peak_simulated_c": _peak_temperature(samples, "simulated_c"),
            "peak_derived_risk": max(risk_values) if risk_values else None,
            "time_above_high_threshold_measured_s": _time_above(samples, threshold, "measured_c"),
            "time_above_high_threshold_simulated_s": _time_above(samples, threshold, "simulated_c"),
            "high_threshold_c": threshold,
            "average_eligible_guest_cpu_utilization_pct": average_cpu,
            "requested_decisions": requested,
            "applied_actions": applied,
            "successful_affinity_readbacks": successful_readbacks,
            "failed_actions": failed_actions,
            "pacing_count": applied["pace"],
            "pacing_requested_time_s": pace_ms / 1000.0,
            "controller_overhead_s": sum(controller_overhead) if controller_overhead else None,
            "cleanup_status": cleanup,
        },
    }
    return result


def compare_runs(baseline: dict[str, Any], aware: dict[str, Any]) -> dict[str, Any]:
    """Flag incompatible or unverifiable environment/config pairs."""

    mismatches: list[str] = []
    unknown: list[str] = []
    left = baseline.get("metadata", {})
    right = aware.get("metadata", {})
    for field in _COMPATIBILITY_FIELDS:
        lhs, rhs = left.get(field), right.get(field)
        if lhs in (None, "") or rhs in (None, ""):
            unknown.append(field)
        elif _canonical(lhs) != _canonical(rhs):
            mismatches.append(f"{field} differs")
    return {
        "compatible": False if mismatches else (None if unknown else True),
        "mismatches": mismatches,
        "unknown_compatibility_fields": unknown,
        "delta_aware_minus_baseline": _deltas(baseline.get("metrics", {}), aware.get("metrics", {})),
    }


def _deltas(baseline: dict[str, Any], aware: dict[str, Any]) -> dict[str, float | None]:
    delta: dict[str, float | None] = {}
    for key, value in aware.items():
        left = _number(baseline.get(key))
        right = _number(value)
        delta[key] = right - left if left is not None and right is not None else None
    return delta


def _csv_text(baseline: dict[str, Any], aware: dict[str, Any], comparison: dict[str, Any]) -> str:
    metric_names = list(baseline["metrics"])
    fields = ["label", "comparison_compatible", *metric_names]
    from io import StringIO

    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for result in (baseline, aware):
        writer.writerow({"label": result["label"], "comparison_compatible": comparison["compatible"], **result["metrics"]})
    return stream.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare ThermoSched baseline and aware event logs.")
    parser.add_argument("--baseline", required=True, type=Path, help="baseline JSONL or CSV event log")
    parser.add_argument("--aware", required=True, type=Path, help="aware JSONL or CSV event log")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--output", type=Path, help="write summary to this file (default: stdout)")
    parser.add_argument("--high-threshold-c", type=float, help="override high-temperature threshold in Celsius")
    parser.add_argument("--require-compatible", action="store_true", help="exit 2 unless compatibility is verified")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        baseline = summarize_run(args.baseline, label="baseline", high_threshold_c=args.high_threshold_c)
        aware = summarize_run(args.aware, label="aware", high_threshold_c=args.high_threshold_c)
        comparison = compare_runs(baseline, aware)
        if args.require_compatible and comparison["compatible"] is not True:
            logger.error("run compatibility was not verified: %s", comparison)
            return 2
        if args.format == "json":
            rendered = json.dumps({"comparison": comparison, "runs": {"baseline": baseline, "aware": aware}}, indent=2, sort_keys=True) + "\n"
        else:
            rendered = _csv_text(baseline, aware, comparison)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError) as exc:
        logger.error("cannot compare event logs: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
