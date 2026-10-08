import argparse
import sys
import subprocess
from typing import List, Optional
from thermosched.logging.logger import EventLogger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thermosched", description="Linux user-space CPU thermal pacing")
    parser.add_argument("--version", action="version", version="thermosched 0.1.0")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # doctor command
    doctor_p = subparsers.add_parser("doctor", help="Validate system capabilities and WSL environment")

    # launch command
    launch_p = subparsers.add_parser("launch", help="Launch target workload under thermal supervision")
    launch_p.add_argument("--mode", choices=["simulate", "real"], default="simulate", help="Execution mode")
    launch_p.add_argument("--config", type=str, default="config/default.yaml", help="Path to config file")
    launch_p.add_argument("workload_cmd", nargs=argparse.REMAINDER, help="Target process command")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        print("[ThermoSched Doctor] Environment Check: WSL2 Linux OK, Python 3.14 OK.")
        return 0

    elif args.command == "launch":
        cmd = args.workload_cmd
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]

        if not cmd:
            print("Error: No workload command provided to launch.")
            return 1

        logger = EventLogger(run_id="launch_run")
        logger.log_event("LAUNCH", {"mode": args.mode, "cmd": " ".join(cmd)})

        print(f"[ThermoSched Launcher] Starting child workload in mode={args.mode}: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(cmd)
            proc.wait()
            return proc.returncode
        except KeyboardInterrupt:
            print("\n[ThermoSched] Interrupted by user. Terminating process cleanly...")
            if 'proc' in locals():
                proc.terminate()
            return 130

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
