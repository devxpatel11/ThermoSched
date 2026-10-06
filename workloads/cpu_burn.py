import argparse
import time

def main():
    parser = argparse.ArgumentParser(description="Deterministic single-process CPU burn workload.")
    parser.add_argument("--seconds", type=float, default=10.0, help="Duration to run in seconds.")
    args = parser.parse_args()

    print(f"[cpu_burn] Starting CPU burn workload for {args.seconds} seconds...")
    start_time = time.time()
    end_time = start_time + args.seconds

    count = 0
    last_log = start_time

    while time.time() < end_time:
        count += 1
        current_time = time.time()
        if current_time - last_log >= 2.0:
            elapsed = round(current_time - start_time, 1)
            print(f"[cpu_burn] Progress: {elapsed}/{args.seconds}s elapsed (iterations: {count})")
            last_log = current_time

    print(f"[cpu_burn] Completed successfully. Total iterations: {count}")

if __name__ == "__main__":
    main()
