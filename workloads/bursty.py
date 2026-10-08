import argparse
import time

def main():
    parser = argparse.ArgumentParser(description="Bursty CPU workload generator.")
    parser.add_argument("--seconds", type=float, default=10.0, help="Total execution duration.")
    parser.add_argument("--burst-duration", type=float, default=1.0, help="Burst compute phase duration.")
    parser.add_argument("--sleep-duration", type=float, default=1.0, help="Sleep idle phase duration.")
    args = parser.parse_args()

    print(f"[bursty] Starting bursty workload ({args.seconds}s total)...")
    start_time = time.time()
    end_time = start_time + args.seconds
    cycle = 1

    while time.time() < end_time:
        burst_end = min(time.time() + args.burst_duration, end_time)
        iterations = 0
        while time.time() < burst_end:
            iterations += 1

        elapsed = round(time.time() - start_time, 1)
        print(f"[bursty] Cycle {cycle}: completed burst ({iterations} iterations) at {elapsed}s")

        remaining_time = end_time - time.time()
        if remaining_time <= 0:
            break

        sleep_time = min(args.sleep_duration, remaining_time)
        time.sleep(sleep_time)
        cycle += 1

    print("[bursty] Completed successfully.")

if __name__ == "__main__":
    main()
