"""Disposable single-process CPU workload for live demonstrations."""

from __future__ import annotations

import argparse
import json
import math
import signal
import time
from collections.abc import Sequence
from pathlib import Path


def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and positive")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wall-clock single-process CPU burn workload.")
    parser.add_argument("--seconds", type=_positive_seconds, default=10.0, help="Duration to run in seconds.")
    parser.add_argument("--progress-jsonl", type=Path, help="append cumulative work progress for a managed experiment")
    args = parser.parse_args(argv)

    print(f"[cpu_burn] Starting CPU burn workload for {args.seconds} seconds...")
    started = time.monotonic()
    deadline = started + args.seconds
    count = 0
    last_log = started
    stopped = False

    def record_progress(completed: int, finished: bool = False) -> None:
        if args.progress_jsonl is None:
            return
        record = {
            "event_type": "WORKLOAD_PROGRESS",
            "monotonic_s": time.monotonic(),
            "completed_units": completed,
            "work_unit": "iterations",
            "finished": finished,
        }
        args.progress_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.progress_jsonl.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    old_sigterm = None
    if args.progress_jsonl is not None:
        old_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, request_stop)
        record_progress(0)

    while time.monotonic() < deadline and not stopped:
        count += 1
        now = time.monotonic()
        if now - last_log >= 2.0:
            print(f"[cpu_burn] Progress: {now - started:.1f}/{args.seconds}s elapsed (iterations: {count})")
            record_progress(count)
            last_log = now
    record_progress(count, finished=not stopped)
    if old_sigterm is not None:
        signal.signal(signal.SIGTERM, old_sigterm)
    if stopped:
        print(f"[cpu_burn] Stopped by manager. Total iterations: {count}")
    else:
        print(f"[cpu_burn] Completed successfully. Total iterations: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
