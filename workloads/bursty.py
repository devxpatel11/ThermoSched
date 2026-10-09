"""Disposable alternating CPU-burst and idle workload."""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence


def _positive(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("duration must be finite and positive")
    return parsed


def _non_negative(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("duration must be finite and non-negative")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wall-clock bursty CPU workload generator.")
    parser.add_argument("--seconds", type=_positive, default=10.0, help="Total execution duration.")
    parser.add_argument("--burst-duration", type=_positive, default=1.0, help="Compute phase duration.")
    parser.add_argument("--sleep-duration", type=_non_negative, default=1.0, help="Idle phase duration.")
    args = parser.parse_args(argv)

    print(f"[bursty] Starting bursty workload ({args.seconds}s total)...")
    started = time.monotonic()
    deadline = started + args.seconds
    cycle = 1
    while time.monotonic() < deadline:
        burst_deadline = min(time.monotonic() + args.burst_duration, deadline)
        iterations = 0
        while time.monotonic() < burst_deadline:
            iterations += 1
        print(f"[bursty] Cycle {cycle}: completed burst ({iterations} iterations) at {time.monotonic() - started:.1f}s")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.sleep_duration, remaining))
        cycle += 1
    print("[bursty] Completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
