# ThermoSched

ThermoSched is an Operating Systems project for a Linux user-space CPU thermal-pacing controller. The project keeps Windows as the host OS and develops and demonstrates the project inside Ubuntu WSL 2.

## Current status

The shared repository foundation, A1–A4 contracts/policy/actuation/controller, B1–B3 sensors/telemetry/simulation, and C1–C3 workloads/logging/dashboard/child launcher are implemented. A4 uses the merged B/C modules directly; it does not carry parallel telemetry, simulation, logging, dashboard, or workload implementations. WSL E0 evidence still needs to be recorded by each member.

Do not treat a command from the project playbook as implemented until the corresponding files are merged into `main` and CI passes.

## Scope lock

- Build a Python 3.10+ Linux user-space controller; do not modify the kernel scheduler.
- Use explicit simulation as the primary WSL acceptance mode. Real sensors are optional.
- Control only workloads launched or explicitly verified by ThermoSched. Never target arbitrary system processes.
- Treat WSL CPU IDs as guest virtual CPUs. An affinity change does not prove movement to a cooler physical Windows core.
- Label every thermal value as measured, simulated, or derived risk. Never present modeled data as measured host temperature.
- Restore the original affinity and resume a paused workload on every handled exit or failure path.

## Start here

1. Complete the [Windows and WSL setup](docs/wsl_setup.md).
2. Record E0 evidence in [the environment matrix](docs/environment_matrix.md) before feature work.
3. Read [the work plan](docs/work_plan.md) for ownership and dependencies.
4. Follow [the contribution and integration rules](CONTRIBUTING.md).
5. Use the branch assigned to your task; do not implement directly on `main`. All Person A work uses the persistent `devpatel` branch exclusively.

Collaborators with write access may merge a pull request once required CI passes, the branch is current with `main`, and review conversations are resolved. Branch protection does not require a separate approval.

The agreed repository baseline is Python 3.12. Code must remain compatible with Python 3.10 or newer, and CI checks the minimum supported version.

Inside Ubuntu WSL 2, install and validate the current scaffold with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m thermosched --help
python -m pytest -q
```

Routine commands require no root privileges. `config/default.yaml` contains demonstration defaults, not universal hardware safety limits. `load_config()` validates overrides before use.

## Shared contracts

- `thermosched.models` defines validated core/process samples, thermal snapshots, decisions, environment metadata, actuator capability status, fixed replay frames, and model-to-guest CPU mapping.
- `thermosched.sensors.base.SensorProvider` keeps hardware access outside pure scheduling logic.
- `thermosched.scheduler.actuator.Actuator` separates OS mechanisms from policy.
- `ManagedPidRegistry` issues targets only for explicitly registered child PIDs and validates requested CPUs against their original eligible guest mask.
- Measured Celsius, simulated Celsius, and derived risk remain distinct provenance values.

## Linux thermal sensor discovery

`thermosched.sensors.discover_linux_thermal_sensors()` performs read-only discovery under `/sys/class/thermal` and `/sys/class/hwmon`. It returns each source path, sensor name, availability, Celsius value, and a conservative scope label (`package`, `core`, `zone`, or `unknown`). The parser recognizes the Linux thermal-zone and hwmon `temp*_input` millidegree-Celsius semantics; missing, inaccessible, and malformed inputs are reported without inventing readings. `python -m thermosched doctor` renders the source paths, scope, provenance, guest CPU mask, actual runtime versions, and current actuator status.

WSL may expose no thermal sensors, or only package/zone-level readings. Such a reading is not per-core or Windows-host temperature. Explicit simulation continues to work without sensors; real mode reports a clear unsupported-source error when no suitable sensor is available. Sensor discovery returns source-aware inventory and does not relabel package readings as measured per-core temperatures.

## CPU telemetry

`thermosched.telemetry.CpuTelemetryCollector` produces one timestamped snapshot with guest logical-CPU utilization, eligible CPU IDs, and optional state for a registered same-user direct child. The first nonblocking psutil sample is marked `priming` and has no utilization value. Process utilization can exceed 100% for multi-threaded workloads and is kept unclamped. Missing processes, PID reuse, and access failures have explicit statuses. Original and current affinity masks are observational; this collector does not change affinity or pause processes. `TemperatureTrend` calculates per-CPU Celsius-per-second changes from timestamped readings, while `ExponentialMovingAverage` supports rolling intensity. WSL CPU IDs describe guest virtual CPUs, not Windows physical-core topology.

## Simulation and sensor fallback

`SimulatedThermalSensor` evolves per-guest-CPU simulated Celsius state using per-CPU utilization, configured heat/cooling rates, assigned guest CPU, pacing duty cycle, and pause state. Initial temperatures are listed in model order and mapped to the actual eligible guest mask; runtime CPU IDs are guest IDs. State can be reset before a replay or comparison run. A seed reproduces configured initial jitter; fixed inputs, timestamps, and config make replay repeatable. This does not make live utilization or wall-clock runs deterministic.

`select_thermal_backend()` supports `simulate`, `auto`, and `real`. Explicit simulation never probes or requires sensors. Auto and real modes refresh default Linux discovery for every sample. They retain raw readable package/core/zone Celsius evidence separately and produce `risk_only` core values; they never label package or unmapped core sensors as measured per-core temperatures. Auto switches to `auto-simulate-fallback` if no usable sensor exists or the measured source disappears. Real mode raises `ThermalSensorUnavailableError`. The named `config/demo_migration.yaml` and `config/demo_all_hot.yaml` fixtures define deterministic hot/cool and all-hot starting conditions. Their model CPU positions map to whatever eligible guest IDs are supplied at runtime.

## Workloads, logging, and terminal output

`workloads/cpu_burn.py` and `workloads/bursty.py` are disposable wall-clock workloads. They validate finite durations and are not deterministic replay tools. `EventLogger` records thermal provenance, backend, eligible guest CPUs, and requested versus applied actions in CSV and JSONL. `TerminalDashboard` labels simulated Celsius, measured Celsius, derived risk, and unknown values separately.

## Run metrics and comparisons

`python -m thermosched.metrics` compares a baseline JSONL/CSV event log with an aware run and writes compact JSON or CSV. JSONL is preferred because it retains nested per-core samples and run metadata. The integrated demo records Windows/WSL version strings from `THERMOSCHED_WINDOWS_VERSION` and `THERMOSCHED_WSL_VERSION` when supplied, plus Ubuntu/kernel, Python/psutil, eligible guest mask, config hash, seed, backend, thermal provenance, workload, and source commit. `cpu_burn.py --progress-jsonl` records cumulative iterations; the integrated demo includes that progress and measured controller CPU time in its JSONL run log.

Temperature outputs stay separate: `peak_measured_per_core_c` includes only explicitly per-core `measured_c` samples, `peak_simulated_c` includes only simulated Celsius, and `peak_derived_risk` is unitless. Package/zone measurements and missing values are never converted into per-core temperatures. Time above threshold is elapsed run time where at least one matching CPU sample is above the threshold; it is not summed across CPUs. Durations use seconds, temperatures use Celsius, CPU utilization uses percent, pacing request time uses seconds, and throughput uses workload units per second. `pacing_requested_time_s` sums successful `pace_ms` requests and does not claim to be a separate wall-clock measurement. CSV-only logs retain the older fixed event fields, so metadata, nested per-core utilization, workload progress, and controller overhead unavailable in a CSV remain `null`.

```bash
python -m thermosched.metrics --baseline results/baseline.jsonl --aware results/aware.jsonl --format json --output results/comparison.json --high-threshold-c 75
python -m thermosched.metrics --baseline results/baseline.jsonl --aware results/aware.jsonl --format csv --output results/comparison.csv --high-threshold-c 75
```

The comparison flags mismatched environment, guest CPU mask, config hash, threshold, sampling interval, workload, or seed. `--require-compatible` exits with status 2 unless these fields are present and match. Deltas are arithmetic comparisons only; simulated temperature deltas do not represent host thermal improvement. For Windows host version values, set `THERMOSCHED_WINDOWS_VERSION` and `THERMOSCHED_WSL_VERSION` before running the demo when they cannot be detected from Ubuntu.

## Managed Linux actuation and A4 integration

`LinuxActuator` registers only a same-user direct child launched by ThermoSched. It records the PID, creation time, parent, owner, and full original guest affinity mask; every operation rechecks identity to reject PID reuse. PID 0, PID 1, the controller, its parent shell, unregistered processes, and CPU IDs outside the captured eligible mask are rejected. Affinity changes require exact readback. Bounded pacing uses `SIGSTOP` followed by `SIGCONT`.

`Controller` injects B2 telemetry, the B3 thermal backend, A2 policy, A3 actuation, the C2 logger, and the C2 dashboard. It samples on monotonic time, maintains per-PID state, distinguishes requested from applied actions, and degrades to skipped control if an input component fails. Cleanup resumes and restores the managed child after normal completion, handled errors, SIGINT, and SIGTERM. SIGKILL, WSL termination, Windows shutdown, or VM failure cannot run in-process cleanup.

The fixed-clock five-minute soak uses synthetic time and inputs. Live WSL runs use wall-clock telemetry and are evaluated by observed events and affinity readback; a seed does not make them identical.

## Pure scheduling policy

`thermosched.scheduler.policy.evaluate_policy()` accepts a fixed thermal snapshot, process sample, policy state, monotonic timestamp, configuration, and eligible guest CPU mask. It returns a decision and next policy state without reading hardware, sleeping, or calling OS APIs.

Risk combines normalized thermal state, CPU utilization, and positive thermal trend using configurable weights. Migration requires hot-sample confirmation, a safe destination, minimum risk improvement, minimum residency, and cooldown. Hysteresis prevents threshold oscillation. If every eligible candidate is unsafe, the policy requests a bounded micro-break; it never pauses the process itself.

Candidate CPU IDs are WSL guest virtual CPUs. Policy output does not prove physical Windows-core placement, and risk remains a modeled/derived scheduling signal rather than measured host temperature. A controller must map fixture IDs to the managed child's original eligible mask and call `record_migration()` only after successful affinity readback.

## CLI contract

The doctor, child launcher, and integrated demo are implemented. `launch --mode baseline` runs a child without thermal control; other launch modes validate and report the selected backend. The `demo` command runs the complete controller against its own disposable child and writes CSV, JSONL, and summary evidence under the selected output directory.

```bash
python -m thermosched doctor
python -m thermosched launch --mode baseline --config config/default.yaml -- python workloads/cpu_burn.py --seconds 10
python -m thermosched demo --scenario migration --config config/demo_migration.yaml --duration 5
python -m thermosched demo --scenario all-hot --config config/demo_all_hot.yaml --duration 5
```

The end-to-end experiment driver remains planned for D5-B5/D5-C5:

```bash
bash scripts/run_demo.sh
```

The final README will be reconciled against the frozen implementation by D5-C5. Until then, each PR updates only the commands and status it actually changes.

## Development checklist

- D1-A1: complete — `python -m pytest -q`
- D2-A2: complete — `python -m pytest -q tests/test_policy.py`, then `python -m pytest -q`
- D3-A3: complete — `.venv/bin/python -m pytest -q tests/test_linux_actuator.py` (8 passed), then the full suite; live WSL child affinity, bounded pacing, SIGINT/SIGTERM cleanup, and exact full-mask restoration passed.
- D4-A4: complete — focused controller/CLI/integration tests passed, the 300-second fixed-clock soak passed, and `.venv/bin/python -m pytest -q` reported 106 passed. Both live named demos completed with zero failures and restored the managed child's 20-CPU original mask.
- D4-B4: complete — `.venv/bin/python -m pytest -q tests/test_metrics.py` (11 passed), then `.venv/bin/python -m pytest -q` (117 passed). A live WSL migration log passed strict compatibility parsing with provenance, workload throughput, action/readback counts, elapsed threshold time, controller overhead, and restored cleanup intact.
- D1-B1: complete — `.venv/bin/python -m pytest -q tests/test_sensors.py`, then `.venv/bin/python -m pytest -q`; WSL reported no thermal inputs and the backend degraded explicitly.
- D2-B2: complete — `.venv/bin/python -m pytest -q tests/test_telemetry.py` (11 passed), then `.venv/bin/python -m pytest -q` (58 passed); live WSL child telemetry preserved the original 20-CPU guest mask.
- D3-B3: complete — focused B3 tests: 22 passed; combined B1–B3 tests: 39 passed; full suite: 72 passed. WSL auto-fallback, named migration/all-hot actions, a deterministic 300-second fixed-clock soak, and live B1→B2→B3 integration passed.
- D1-C1: complete
- D2-C2: complete
- D3-C3: complete
- B1–B3/C1–C3 integration review: complete — focused WSL tests: 57 passed; full suite: 88 passed. Named scenarios, live child sampling, SIGTERM cleanup, actual CLI launches, fixed-clock soak, and simulation benchmark passed.
