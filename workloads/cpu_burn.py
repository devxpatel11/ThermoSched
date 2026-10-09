"""Disposable single-process CPU workload for live demonstrations."""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence


def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and positive")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wall-clock single-process CPU burn workload.")
    parser.add_argument("--seconds", type=_positive_seconds, default=10.0, help="Duration to run in seconds.")
    args = parser.parse_args(argv)

    print(f"[cpu_burn] Starting CPU burn workload for {args.seconds} seconds...")
    started = time.monotonic()
    deadline = started + args.seconds
    count = 0
    last_log = started
    while time.monotonic() < deadline:
        count += 1
        now = time.monotonic()
        if now - last_log >= 2.0:
            print(f"[cpu_burn] Progress: {now - started:.1f}/{args.seconds}s elapsed (iterations: {count})")
            last_log = now
    print(f"[cpu_burn] Completed successfully. Total iterations: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
