# ThermoSched repository rules

These rules apply to every coding agent and every path in this repository.

1. Work on one task ID from `docs/work_plan.md` at a time and stay within its owned paths. Preserve public interfaces unless a separate coordinated contract PR changes them first.
2. Keep this an academic Linux user-space prototype for Ubuntu WSL 2. Do not add a native Windows actuator, kernel changes, cgroups, uclamp, eBPF, multi-host support, or other stretch work before the core path is complete.
3. Never add code that pauses, kills, reprioritizes, or repins arbitrary system processes. The default target is a child workload launched by ThermoSched. Any optional PID mode must verify identity, ownership, and eligibility.
4. Keep hardware access behind interfaces. Pure policy behavior must be testable with synthetic snapshots, a fake clock, and fixed inputs.
5. Every thermal value must carry provenance: measured, simulated, or derived risk. A package or zone reading must never be labeled as a measured per-core or Windows-host temperature.
6. Map model CPU IDs to the managed child's original eligible guest mask before actuation. Read back applied affinity and distinguish requested actions from successful actions.
7. Every affinity or pause path must restore original affinity and resume the process after handled errors, SIGINT, and SIGTERM. Do not claim recovery after SIGKILL or guest/host shutdown.
8. Add focused pytest coverage for behavioral changes. Run the focused tests first and `python -m pytest -q` before completion. Report failures and skipped hardware checks honestly.
9. Keep fixed-input deterministic replay separate from live utilization and wall-clock experiments. A random seed alone does not make live runs deterministic.
10. Keep README commands synchronized with the implemented CLI. Do not commit generated logs, virtual environments, caches, machine-specific paths, or invented validation results.
11. Avoid unrelated refactors and speculative abstractions. Follow the PR, branch, ownership, and integration rules in `CONTRIBUTING.md`.
