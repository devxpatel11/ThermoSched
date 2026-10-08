# ThermoSched

ThermoSched is an Operating Systems project for a Linux user-space CPU thermal-pacing controller. The project keeps Windows as the host OS and develops and demonstrates the project inside Ubuntu WSL 2.

## Current status

The shared repository foundation, D1-A1 contracts, D2-A2 pure policy, D1-B1 Linux thermal sensor discovery, D2-B2 CPU telemetry, and D3-B3 thermal simulation/fallback are implemented. Workload generators, actuation, orchestration, and demo scripts remain assigned work in [docs/work_plan.md](docs/work_plan.md). WSL E0 environment evidence still needs to be recorded by each member.

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

`thermosched.sensors.discover_linux_thermal_sensors()` performs read-only discovery under `/sys/class/thermal` and `/sys/class/hwmon`. It returns each source path, sensor name, availability, Celsius value, and a conservative scope label (`package`, `core`, `zone`, or `unknown`). The parser recognizes the Linux thermal-zone and hwmon `temp*_input` millidegree-Celsius semantics; missing, inaccessible, and malformed inputs are reported without inventing readings. `format_sensor_inventory()` renders the same paths and scope for a future doctor/debug command.

WSL may expose no thermal sensors, or only package/zone-level readings. Such a reading is not per-core or Windows-host temperature. Explicit simulation must continue to work without sensors; a future real-mode command should report a clear unsupported-source error when no suitable sensor is available. Current sensor discovery returns source-aware inventory; it does not yet adapt package readings into the per-core `ThermalSnapshot` contract.

## CPU telemetry

`thermosched.telemetry.CpuTelemetryCollector` produces one timestamped snapshot with guest logical-CPU utilization, eligible CPU IDs, and optional state for a registered same-user direct child. The first nonblocking psutil sample is marked `priming` and has no utilization value. Process utilization can exceed 100% for multi-threaded workloads and is kept unclamped. Missing processes, PID reuse, and access failures have explicit statuses. Original and current affinity masks are observational; this collector does not change affinity or pause processes. `TemperatureTrend` calculates per-CPU Celsius-per-second changes from timestamped readings, while `ExponentialMovingAverage` supports rolling intensity. WSL CPU IDs describe guest virtual CPUs, not Windows physical-core topology.

## Simulation and sensor fallback

`SimulatedThermalSensor` evolves per-guest-CPU simulated Celsius state using per-CPU utilization, configured heat/cooling rates, assigned guest CPU, pacing duty cycle, and pause state. Initial temperatures are listed in model order and mapped to the actual eligible guest mask; runtime CPU IDs are guest IDs. State can be reset before a replay or comparison run. A seed reproduces configured initial jitter; fixed inputs, timestamps, and config make replay repeatable. This does not make live utilization or wall-clock runs deterministic.

`select_thermal_backend()` supports `simulate`, `auto`, and `real`. Explicit simulation never probes or requires sensors. Auto mode uses readable package/core/zone sensors as measured evidence and produces separate `risk_only` core values; raw Celsius readings remain attached to the `ThermalBackendFrame.measured_readings` field. It does not label package or unmapped core sensors as per-core measurements. If no usable sensor exists, auto announces `auto-simulate-fallback`; real mode raises `ThermalSensorUnavailableError`. The named `config/demo_migration.yaml` and `config/demo_all_hot.yaml` fixtures define deterministic hot/cool and all-hot starting conditions. Their model CPU positions map to whatever eligible guest IDs are supplied at runtime.

## Pure scheduling policy

`thermosched.scheduler.policy.evaluate_policy()` accepts a fixed thermal snapshot, process sample, policy state, monotonic timestamp, configuration, and eligible guest CPU mask. It returns a decision and next policy state without reading hardware, sleeping, or calling OS APIs.

Risk combines normalized thermal state, CPU utilization, and positive thermal trend using configurable weights. Migration requires hot-sample confirmation, a safe destination, minimum risk improvement, minimum residency, and cooldown. Hysteresis prevents threshold oscillation. If every eligible candidate is unsafe, the policy requests a bounded micro-break; it never pauses the process itself.

Candidate CPU IDs are WSL guest virtual CPUs. Policy output does not prove physical Windows-core placement, and risk remains a modeled/derived scheduling signal rather than measured host temperature. A controller must map fixture IDs to the managed child's original eligible mask and call `record_migration()` only after successful affinity readback.

## Planned command contract

Only `python -m thermosched --help` and `--version` are implemented by A1. The following commands remain targets for their assigned implementation tasks:

```bash
python -m thermosched doctor
python -m thermosched launch --mode simulate --config config/demo_migration.yaml -- python workloads/cpu_burn.py --seconds 60
python -m thermosched launch --mode simulate --config config/demo_all_hot.yaml -- python workloads/cpu_burn.py --seconds 60
bash scripts/run_demo.sh
python scripts/compare_runs.py --baseline logs/baseline.csv --aware logs/aware.csv
```

The final README will be reconciled against the frozen implementation by D5-C5. Until then, each PR updates only the commands and status it actually changes.

## Development checklist

- D1-A1: complete — `python -m pytest -q`
- D2-A2: complete — `python -m pytest -q tests/test_policy.py`, then `python -m pytest -q`
- D1-B1: complete — `.venv/bin/python -m pytest -q tests/test_sensors.py`, then `.venv/bin/python -m pytest -q`; WSL reported no thermal inputs and the backend degraded explicitly.
- D2-B2: complete — `.venv/bin/python -m pytest -q tests/test_telemetry.py` (11 passed), then `.venv/bin/python -m pytest -q` (58 passed); live WSL child telemetry preserved the original 20-CPU guest mask.
- D3-B3: complete — `.venv/bin/python -m pytest -q tests/test_simulated_sensors.py tests/test_config.py` (20 passed), then `.venv/bin/python -m pytest -q` (70 passed). Fixed-input migration and all-hot fixtures returned `migrate` and bounded `pace` respectively.
- D1-C1: complete
- D2-C2: complete
- D3-C3: complete
