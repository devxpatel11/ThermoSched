# ThermoSched

ThermoSched is a six-day Operating Systems project for a Linux user-space CPU thermal-pacing controller. The team keeps Windows as the host OS and develops and demonstrates the project inside Ubuntu WSL 2.

## Current status

The shared repository foundation and Member A tasks D1-A1 through D4-A4 are implemented. A3 provides guarded Linux child actuation. A4 provides the dependency-injected controller and explicit WSL simulation adapters; the assigned B/C production sensor, telemetry, logging/dashboard, workload, and CLI modules remain separate work in [docs/work_plan.md](docs/work_plan.md).

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

## Pure scheduling policy

`thermosched.scheduler.policy.evaluate_policy()` accepts a fixed thermal snapshot, process sample, policy state, monotonic timestamp, configuration, and eligible guest CPU mask. It returns a decision and next policy state without reading hardware, sleeping, or calling OS APIs.

Risk combines normalized thermal state, CPU utilization, and positive thermal trend using configurable weights. Migration requires hot-sample confirmation, a safe destination, minimum risk improvement, minimum residency, and cooldown. Hysteresis prevents threshold oscillation. If every eligible candidate is unsafe, the policy requests a bounded micro-break; it never pauses the process itself.

Candidate CPU IDs are WSL guest virtual CPUs. Policy output does not prove physical Windows-core placement, and risk remains a modeled/derived scheduling signal rather than measured host temperature. A controller must map fixture IDs to the managed child's original eligible mask and call `record_migration()` only after successful affinity readback.

## Managed Linux actuation

`LinuxActuator` accepts only a direct child of the current controller with the same Linux UID. Registration records its PID, creation time, parent, owner, and full original affinity mask. Every operation rechecks that identity to reject PID reuse. PID 0, PID 1, the controller, the parent shell, unregistered processes, and CPUs outside the captured eligible guest mask are rejected.

Affinity changes require exact readback. Pacing uses bounded `SIGSTOP`/`SIGCONT`, and cleanup resumes the child before restoring its original mask. Handled exceptions, SIGINT, and SIGTERM run restoration. SIGKILL, forced WSL termination, Windows shutdown, and VM failure cannot execute Python cleanup; after such an event, terminate the disposable workload or restart WSL before another run.

## A4 integrated WSL simulation

Run these commands inside the Ubuntu repository virtual environment. `doctor` reports guest CPU scope, model mapping, simulated thermal provenance, backend names, and actuator capability:

```bash
python -m thermosched.demo doctor
python -m thermosched.demo run --scenario migration --config config/demo_migration.yaml --duration 5
python -m thermosched.demo run --scenario all-hot --config config/demo_all_hot.yaml --duration 5
```

Each run launches its own disposable Linux child. The migration scenario requires at least two eligible guest CPUs and reports unsupported status otherwise. The all-hot scenario requests bounded pacing and verifies resume. JSON Lines events record original/requested/observed masks, requested versus applied action, backend, environment, eligible guest CPUs, and `simulated_c` provenance. Generated evidence is written under `results/` and is ignored by Git.

The simulation model is coupled to the managed child's assignment and observed duty cycle. Its values are modeled guest inputs, not measured per-core or Windows-host temperatures. The 300-second automated soak uses an injected fixed clock and synthetic telemetry; it is separate from live wall-clock utilization runs and makes no repeatability claim about them.

Member A's recorded WSL acceptance run also completed a separate 300-second wall-clock migration soak: 3,000 samples, one verified guest-CPU migration, zero failures, and exact restoration of the original affinity mask.

## Remaining planned command contract

The general launch, workload, and comparison commands remain targets for their assigned B/C tasks:

```bash
python -m thermosched launch --mode simulate --config config/demo_migration.yaml -- python workloads/cpu_burn.py --seconds 60
python -m thermosched launch --mode simulate --config config/demo_all_hot.yaml -- python workloads/cpu_burn.py --seconds 60
bash scripts/run_demo.sh
python scripts/compare_runs.py --baseline logs/baseline.csv --aware logs/aware.csv
```

The final README will be reconciled against the frozen implementation by D5-C5. Until then, each PR updates only the commands and status it actually changes.

## Development checklist

- D1-A1: complete — `python -m pytest -q`
- D2-A2: complete — `python -m pytest -q tests/test_policy.py`, then `python -m pytest -q`
- D3-A3: complete — `python -m pytest -q tests/test_safety.py tests/test_linux_actuator.py`, then `python -m pytest -q`
- D4-A4: complete — `python -m pytest -q tests/test_controller.py`, then `python -m pytest -q`; live WSL runs use both named demo configs
