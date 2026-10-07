"""Safe WSL demonstration entry point for A3/A4 integration evidence."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import psutil

from thermosched.config import load_config
from thermosched.controller import (
    ConsoleDashboard,
    Controller,
    JsonLineSink,
    PsutilTelemetryProvider,
)
from thermosched.models import CpuIdMap
from thermosched.scheduler.actuator import LinuxActuator
from thermosched.simulation import SimulatedThermalProvider

logger = logging.getLogger(__name__)


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return False


def _environment(eligible: tuple[int, ...]) -> str:
    platform_name = "WSL2" if _is_wsl() else "Linux"
    return f"{platform_name} kernel={platform.release()} guest_cpu_scope={eligible}"


def doctor() -> int:
    if not sys.platform.startswith("linux"):
        print("supported=false reason=Ubuntu WSL 2 or Linux guest required")
        return 2
    eligible = tuple(sorted(psutil.Process().cpu_affinity()))
    actuator = LinuxActuator()
    capabilities = actuator.capabilities()
    print(f"environment={_environment(eligible)}")
    print(f"python={platform.python_version()} psutil={psutil.__version__}")
    print(f"eligible_guest_cpus={eligible}")
    print(f"model_to_guest={CpuIdMap.from_eligible_guest_cpus(eligible).pairs}")
    print("thermal_provenance=simulated_c backend=simulation:assignment-coupled-v1")
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
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "x=0\nwhile True:\n x=(x+1)%1000003",
        ]
    )
    actuator = LinuxActuator()
    try:
        target = actuator.register_child(child.pid)
        if scenario == "migration" and len(target.eligible_guest_cpus) < 2:
            print("unsupported: live migration requires at least two eligible guest CPUs", file=sys.stderr)
            return 2
        cpu_map = CpuIdMap.from_eligible_guest_cpus(target.eligible_guest_cpus)
        actuator.set_affinity(target, (target.eligible_guest_cpus[0],))
        sensor = SimulatedThermalProvider(len(cpu_map.pairs), scenario, config)
        output_dir.mkdir(parents=True, exist_ok=True)
        events_path = output_dir / f"{scenario}.jsonl"
        results_path = output_dir / f"{scenario}-summary.json"
        with events_path.open("w", encoding="utf-8", newline="\n") as events:
            controller = Controller(
                config=config,
                sensor=sensor,
                telemetry=PsutilTelemetryProvider(),
                actuator=actuator,
                event_sink=JsonLineSink(events),
                dashboard=ConsoleDashboard(),
                cpu_map=cpu_map,
                environment=_environment(target.eligible_guest_cpus),
            )
            summary = controller.run(target, max_duration_s=duration_s)
        restored_mask = actuator.get_affinity(target)
        result = {
            **asdict(summary),
            "scenario": scenario,
            "thermal_provenance": "simulated_c",
            "sensor_backend": sensor.name,
            "telemetry_backend": "psutil:guest-vcpu",
            "environment": _environment(target.eligible_guest_cpus),
            "model_to_guest": cpu_map.pairs,
            "original_mask": target.original_affinity,
            "restored_mask": restored_mask,
            "restored": restored_mask == target.original_affinity,
            "events": str(events_path),
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
            os.kill(child.pid, signal.SIGCONT)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ThermoSched WSL simulation and capability evidence")
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
