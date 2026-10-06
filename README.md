# ThermoSched

ThermoSched is a six-day Operating Systems project for a Linux user-space CPU thermal-pacing controller. The team keeps Windows as the host OS and develops and demonstrates the project inside Ubuntu WSL 2.

## Current status

The shared repository foundation is ready. Product implementation has not started: the `thermosched` package, workloads, scenario configuration, tests, and demo scripts belong to the assigned member tasks in [docs/work_plan.md](docs/work_plan.md).

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

## Planned command contract

These commands are targets for the assigned implementation tasks; they are not available in the foundation commit:

```bash
python -m thermosched doctor
python -m thermosched launch --mode simulate --config config/demo_migration.yaml -- python workloads/cpu_burn.py --seconds 60
python -m thermosched launch --mode simulate --config config/demo_all_hot.yaml -- python workloads/cpu_burn.py --seconds 60
python -m pytest -q
bash scripts/run_demo.sh
python scripts/compare_runs.py --baseline logs/baseline.csv --aware logs/aware.csv
```

The final README will be reconciled against the frozen implementation by D5-C5. Until then, each PR updates only the commands and status it actually changes.

## Progress Checklist
- [x] D1-C1: complete
