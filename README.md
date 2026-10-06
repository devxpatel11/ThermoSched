# ThermoSched

ThermoSched is a six-day Operating Systems project for a Linux user-space CPU thermal-pacing controller. The team keeps Windows as the host OS and develops and demonstrates the project inside Ubuntu WSL 2.

## Current status

The shared repository foundation and D1-A1 contracts are implemented. Sensor backends, telemetry, policy, workloads, scenario fixtures, actuation, orchestration, and demo scripts remain assigned work in [docs/work_plan.md](docs/work_plan.md).

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
5. Create the branch assigned to your task; do not implement directly on `main`.

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
- D2-A2: planned
