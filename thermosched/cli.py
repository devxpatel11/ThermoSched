"""Command-line entry points for environment checks and managed child launch."""

from __future__ import annotations

import argparse
import os
import platform
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import psutil

from thermosched import __version__
from thermosched.config import ConfigError, load_config
from thermosched.logging.logger import EventLogger
from thermosched.scheduler.actuator import LinuxActuator
from thermosched.sensors import (
    ThermalSensorUnavailableError,
    discover_linux_thermal_sensors,
    format_sensor_inventory,
    select_thermal_backend,
)


class _SignalExit(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _eligible_guest_cpus() -> tuple[int, ...]:
    try:
        return tuple(sorted(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        try:
            return tuple(sorted(psutil.Process().cpu_affinity()))
        except (AttributeError, NotImplementedError, psutil.Error, OSError):
            return ()


def _is_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    release = platform.release().lower()
    try:
        version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        version = ""
    return "microsoft" in release or "microsoft" in version


def _run_doctor() -> int:
    eligible = _eligible_guest_cpus()
    inventory = discover_linux_thermal_sensors()
    print("ThermoSched doctor")
    print(f"Environment: system={platform.system()} kernel={platform.release()} wsl={str(_is_wsl()).lower()}")
    print(f"Python: {platform.python_version()} | psutil: {psutil.__version__}")
    print(f"Eligible guest CPU IDs: {eligible if eligible else 'unavailable'}")
    if inventory.available:
        scopes = ", ".join(sorted({reading.granularity for reading in inventory.available}))
        print(f"Thermal provenance: measured_c available at scope(s): {scopes}")
    else:
        print("Thermal provenance: measured_c unavailable; simulated_c is the supported fallback")
    print(format_sensor_inventory(inventory))
    capabilities = LinuxActuator().capabilities()
    print(
        "Actuator capability: "
        f"affinity={capabilities.affinity} pause_resume={capabilities.pause_resume} "
        f"restore={capabilities.restore} reasons={capabilities.reasons}"
    )
    return 0 if platform.system() == "Linux" and bool(eligible) and capabilities.restore else 1


def _stop_child(proc: subprocess.Popen[object]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _run_launch(args: argparse.Namespace) -> int:
    cmd = list(args.workload_cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("Error: no workload command provided to launch.", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        eligible = _eligible_guest_cpus()
        if not eligible:
            raise RuntimeError("no eligible guest CPU IDs are available")
        if args.mode == "baseline":
            backend_name = "baseline-no-thermal-control"
            provenance = "none"
        else:
            backend = select_thermal_backend(args.mode, eligible, config)
            backend_name = backend.name
            provenance = backend.thermal_kind.value
        logger = EventLogger()
    except (ConfigError, ThermalSensorUnavailableError, RuntimeError, OSError) as exc:
        print(f"Error: cannot prepare launch: {exc}", file=sys.stderr)
        return 2

    proc: subprocess.Popen[object] | None = None
    old_sigterm = signal.getsignal(signal.SIGTERM)

    def request_stop(signum: int, _frame: object) -> None:
        raise _SignalExit(signum)

    try:
        signal.signal(signal.SIGTERM, request_stop)
        proc = subprocess.Popen(cmd)
        logger.log_event(
            "LAUNCH",
            {
                "mode": args.mode,
                "executable": cmd[0],
                "backend": backend_name,
                "thermal_provenance": provenance,
                "eligible_guest_cpus": eligible,
                "requested_action": "launch",
                "applied_action": "launched",
            },
        )
        print(
            f"[ThermoSched Launcher] child_pid={proc.pid} mode={args.mode} "
            f"backend={backend_name} provenance={provenance} executable={cmd[0]}"
        )
        return proc.wait()
    except KeyboardInterrupt:
        print("\n[ThermoSched] SIGINT received; stopping the managed child.", file=sys.stderr)
        return 130
    except _SignalExit as exc:
        print(f"[ThermoSched] signal {exc.signum} received; stopping the managed child.", file=sys.stderr)
        return 128 + exc.signum
    except OSError as exc:
        print(f"Error: failed to launch managed child: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        if proc is not None:
            _stop_child(proc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thermosched",
        description="Linux user-space CPU thermal pacing scheduler prototype",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="available subcommands")
    subparsers.add_parser("doctor", help="report actual Linux/WSL and thermal capabilities")

    launch = subparsers.add_parser("launch", help="launch an explicitly managed child workload")
    launch.add_argument(
        "--mode",
        choices=["baseline", "simulate", "auto", "real"],
        default="simulate",
        help="select baseline execution or validate a thermal input backend",
    )
    launch.add_argument("--config", default="config/default.yaml", help="validated YAML configuration")
    launch.add_argument("workload_cmd", nargs=argparse.REMAINDER, help="command after --")

    demo = subparsers.add_parser("demo", help="run the integrated A/B/C managed-child demonstration")
    demo.add_argument("--scenario", choices=["migration", "all-hot"], required=True)
    demo.add_argument("--config", type=Path, required=True)
    demo.add_argument("--duration", type=float, default=5.0)
    demo.add_argument("--output-dir", type=Path, default=Path("results"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor()
    if args.command == "launch":
        return _run_launch(args)
    if args.command == "demo":
        if args.duration <= 0:
            print("Error: --duration must be positive.", file=sys.stderr)
            return 2
        from thermosched.demo import run_demo

        return run_demo(args.scenario, args.config, args.duration, args.output_dir)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
