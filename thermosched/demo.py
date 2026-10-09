"""End-to-end WSL demonstration using the merged A, B, and C components."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

import psutil

from thermosched.config import SchedulerConfig, load_config
from thermosched.controller import (
    Controller,
    CpuTelemetryProvider,
    EventLoggerSink,
    TerminalDashboardSink,
)
from thermosched.dashboard.terminal import TerminalDashboard
from thermosched.logging.logger import EventLogger
from thermosched.metrics import build_run_metadata
from thermosched.models import CpuIdMap
from thermosched.scheduler.actuator import LinuxActuator
from thermosched.sensors import discover_linux_thermal_sensors, select_thermal_backend
from thermosched.telemetry import CpuTelemetryCollector

logger = logging.getLogger(__name__)


def _read_progress_events(path: Path) -> list[dict[str, object]]:
    """Read complete JSONL progress records emitted by the managed workload."""

    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid workload progress at line {line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"workload progress at line {line_number} is not an object")
            records.append(record)
    return records


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _environment(eligible: tuple[int, ...]) -> str:
    platform_name = "WSL2" if _is_wsl() else "Linux"
    return f"{platform_name} kernel={platform.release()} guest_cpu_scope={eligible}"


def _scenario_config(config: SchedulerConfig, scenario: str, cpu_count: int) -> SchedulerConfig:
    if scenario != "all-hot":
        return config
    configured = config.simulation.initial_temps_c
    if not configured:
        raise ValueError("all-hot scenario requires at least one initial simulated temperature")
    expanded = configured[:cpu_count] + (configured[-1],) * max(0, cpu_count - len(configured))
    return replace(config, simulation=replace(config.simulation, initial_temps_c=expanded))


def doctor() -> int:
    if not sys.platform.startswith("linux"):
        print("supported=false reason=Ubuntu WSL 2 or Linux guest required")
        return 2
    eligible = tuple(sorted(psutil.Process().cpu_affinity()))
    capabilities = LinuxActuator().capabilities()
    inventory = discover_linux_thermal_sensors()
    backend = "measured sensor-derived risk" if inventory.available else "auto-simulate-fallback"
    provenance = "risk_only with measured evidence" if inventory.available else "simulated_c"
    print(f"environment={_environment(eligible)}")
    print(f"python={platform.python_version()} psutil={psutil.__version__}")
    print(f"eligible_guest_cpus={eligible}")
    print(f"model_to_guest={CpuIdMap.from_eligible_guest_cpus(eligible).pairs}")
    print(f"thermal_provenance={provenance} backend={backend}")
    print(f"sensor_sources={tuple(reading.source_path for reading in inventory.available)}")
    print(
        f"actuator affinity={capabilities.affinity} pause_resume={capabilities.pause_resume} "
        f"restore={capabilities.restore} reasons={capabilities.reasons}"
    )
    print(f"live_migration_eligible={len(eligible) >= 2}")
    return 0 if capabilities.affinity and capabilities.pause_resume and capabilities.restore else 2


def run_demo(scenario: str, config_path: Path, duration_s: float, output_dir: Path) -> int:
    if not sys.platform.startswith("linux"):
        print("unsupported: demo requires Ubuntu WSL 2 or Linux", file=sys.stderr)
        return 2
    config = load_config(config_path)
    workload = Path(__file__).parents[1] / "workloads" / "cpu_burn.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{scenario}-{time.time_ns()}"
    workload_progress_path = output_dir / f"{run_id}-workload.jsonl"
    child = subprocess.Popen(
        [
            sys.executable,
            str(workload),
            "--seconds",
            str(duration_s + 10.0),
            "--progress-jsonl",
            str(workload_progress_path),
        ],
    )
    actuator = LinuxActuator()
    target = None
    try:
        target = actuator.register_child(child.pid)
        if scenario == "migration" and len(target.eligible_guest_cpus) < 2:
            print("unsupported: live migration requires at least two eligible guest CPUs", file=sys.stderr)
            return 2
        cpu_map = CpuIdMap.from_eligible_guest_cpus(target.eligible_guest_cpus)
        config = _scenario_config(config, scenario, len(cpu_map.pairs))

        collector = CpuTelemetryCollector()
        telemetry_mask = collector.register_child(child.pid)
        if telemetry_mask != target.original_affinity:
            raise RuntimeError("B2 telemetry and A3 actuator captured different original masks")
        telemetry = CpuTelemetryProvider(collector)
        sensor = select_thermal_backend("simulate", target.eligible_guest_cpus, config)

        actuator.set_affinity(target, (target.eligible_guest_cpus[0],))
        event_logger = EventLogger(output_dir, run_id)
        run_metadata = build_run_metadata(
            config_path=config_path,
            config=config,
            eligible_guest_cpus=target.eligible_guest_cpus,
            backend=sensor.name,
            source_provenance=sensor.thermal_kind.value,
            workload="cpu_burn",
        )
        event_logger.log_event(
            "RUN_METADATA",
            {
                "pid": target.pid,
                "eligible_guest_cpus": target.eligible_guest_cpus,
                "metadata": run_metadata,
            },
        )
        controller = Controller(
            config=config,
            sensor=sensor,
            telemetry=telemetry,
            actuator=actuator,
            event_sink=EventLoggerSink(event_logger),
            dashboard=TerminalDashboardSink(TerminalDashboard(len(cpu_map.pairs))),
            cpu_map=cpu_map,
            environment=_environment(target.eligible_guest_cpus),
        )
        controller_cpu_started = time.process_time()
        summary = controller.run(target, max_duration_s=duration_s)
        controller_overhead_s = max(0.0, time.process_time() - controller_cpu_started)
        restored_mask = actuator.get_affinity(target)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        try:
            for progress_event in _read_progress_events(workload_progress_path):
                event_type = progress_event.pop("event_type", "WORKLOAD_PROGRESS")
                event_logger.log_event(str(event_type), progress_event)
        except (OSError, ValueError) as exc:
            logger.warning("could not include workload progress in run log %s: %s", workload_progress_path, exc)
        cleanup_status = "restored" if restored_mask == target.original_affinity else "failed"
        event_logger.log_event(
            "RUN_CLEANUP",
            {
                "pid": target.pid,
                "eligible_guest_cpus": target.eligible_guest_cpus,
                "cleanup_status": cleanup_status,
                "restored": cleanup_status == "restored",
                "restoration_error": summary.restoration_error,
                "controller_overhead_s": controller_overhead_s,
                "original_mask": target.original_affinity,
                "observed_mask": restored_mask,
            },
        )
        results_path = output_dir / f"{run_id}-summary.json"
        result = {
            **asdict(summary),
            "scenario": scenario,
            "thermal_provenance": sensor.thermal_kind.value,
            "sensor_backend": sensor.name,
            "telemetry_backend": telemetry.name,
            "logging_backend": "c2:event-logger",
            "dashboard_backend": "c2:terminal-dashboard",
            "workload_backend": "c1:cpu-burn",
            "environment": _environment(target.eligible_guest_cpus),
            "model_to_guest": cpu_map.pairs,
            "original_mask": target.original_affinity,
            "restored_mask": restored_mask,
            "restored": restored_mask == target.original_affinity,
            "cleanup_status": cleanup_status,
            "run_metadata": run_metadata,
            "controller_overhead_s": controller_overhead_s,
            "events_csv": str(event_logger.csv_path),
            "events_jsonl": str(event_logger.jsonl_path),
        }
        results_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        if summary.restoration_error or restored_mask != target.original_affinity:
            return 1
        if scenario == "migration" and summary.migrations < 1:
            print("unsupported: simulation produced no verified migration", file=sys.stderr)
            return 2
        if scenario == "all-hot" and summary.paces < 1:
            print("unsupported: simulation produced no verified pacing", file=sys.stderr)
            return 2
        return 0
    finally:
        if child.poll() is None:
            if target is not None:
                try:
                    actuator.restore(target)
                except Exception as exc:
                    logger.error("demo cleanup could not restore managed child pid=%s: %s", child.pid, exc)
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ThermoSched WSL integration evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="show guest scope, provenance, mapping, and actuator support")
    run = subparsers.add_parser("run", help="run a disposable managed-child simulation")
    run.add_argument("--scenario", choices=("migration", "all-hot"), required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--duration", type=float, default=5.0)
    run.add_argument("--output-dir", type=Path, default=Path("results"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.command == "doctor":
        return doctor()
    if args.duration <= 0:
        raise SystemExit("--duration must be positive")
    return run_demo(args.scenario, args.config, args.duration, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
