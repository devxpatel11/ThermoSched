# Team work plan

This file is the merge order and ownership source of truth. The foundation commit does not complete any member task.

## Day 1 bootstrap gates

| Gate | Owner | Required before | Evidence |
| --- | --- | --- | --- |
| E0-A | A | D1-A1 | WSL VERSION 2, Linux kernel, Python, psutil when installed, and eligible guest CPU mask |
| E0-B | B | D1-B1 | E0 record plus sensor-source and provenance inventory |
| E0-C | C | D1-C1 | E0 record plus portable workload check |
| Live migration | A/B/C | D4-A4 acceptance | At least two eligible guest CPUs and a passing child-affinity readback probe |
| Scenario contract | A/B/C | D3-B3 and D4-A4 | Agreed schemas for `demo_migration.yaml` and `demo_all_hot.yaml` |

## Assigned branches and dependencies

| ID | Owner | Branch | Deliverable | Must be on `main` first |
| --- | --- | --- | --- | --- |
| D1-A1 | A | `feat/d1-a1-repo-contracts` | Package scaffold, contracts, configuration, safety boundary | Foundation only |
| D1-B1 | B | `feat/d1-b1-sensors` | Linux sensor discovery and thermal backend prototype | D1-A1 before merge |
| D1-C1 | C | `feat/d1-c1-workloads` | Workload generators, experiment fixtures, test plan | Foundation only |
| D2-A2 | A | `feat/d2-a2-policy` | Thermal risk scoring and pure scheduling policy | D1-A1 |
| D2-B2 | B | `feat/d2-b2-telemetry` | Per-CPU telemetry and managed-process profiling | D1-A1 |
| D2-C2 | C | `feat/d2-c2-logging-ui` | Event logger and terminal dashboard | D1-A1; mock B2 data is allowed |
| D3-A3 | A | `feat/d3-a3-actuator` | Affinity, bounded pacing, and safe restoration | D1-A1; keep policy-agnostic |
| D3-B3 | B | `feat/d3-b3-simulation` | Simulation fallback and thermal-state estimator | D1-B1, D2-B2 |
| D3-C3 | C | `feat/d3-c3-cli-baseline` | CLI, child launcher, and baseline mode | D1-A1, D1-C1; integrate A3/B1/B2/B3 as available |
| D4-A4 | A | `feat/d4-a4-controller` | Main controller and end-to-end integration | D2-A2, D2-B2, D2-C2, D3-A3, D3-B3, D3-C3 |
| D4-B4 | B | `feat/d4-b4-metrics` | Experiment metrics, run summaries, comparison data | D2-B2, D2-C2, D4-A4 event schema |
| D4-C4 | C | `feat/d4-c4-tests-benchmark` | Automated tests and benchmark script | D2-A2, D3-A3, D3-B3, D3-C3; merge after D4-A4 when possible |
| D5-A5 | A | `feat/d5-a5-hardening` | Anti-thrashing tuning and failure recovery | D4-A4, D4-C4 |
| D5-B5 | B | `feat/d5-b5-experiments` | Controlled experiments and results analysis | D4-A4, D4-B4, D4-C4; use A5 defaults when available |
| D5-C5 | C | `feat/d5-c5-docs-readme` | Final README, architecture docs, and demo script | D4-A4, D5-B5 results |
| D6-A6 | A | `feat/d6-a6-release` | Release integration, code freeze, final tag | D5-A5, D5-B5, D5-C5 |
| D6-B6 | B | `feat/d6-b6-repro-check` | Independent clean-checkout reproduction | D6-A6 release candidate |
| D6-C6 | C | `feat/d6-c6-demo-pack` | Submission pack and demo rehearsal | D6-A6, D6-B6 sign-off, D5-C5 |

The critical path is A1 → A2/A3 + B2/B3 + C2/C3 → A4 → C4/A5/B5/C5 → A6 → B6/C6. Protect A4 and C4 first if the schedule slips. Dashboard polish and optional plots are the first scope to drop.

## Acceptance commitments

- W01: capture each member's environment.
- W02: missing sensors fall back to explicit simulation; explicit real mode fails helpfully.
- W03: migrate within a non-contiguous eligible guest mask and verify affinity readback.
- W04: support deterministic mocked replay on one CPU and record live migration as skipped.
- W05: bound all-hot stop/resume and restore state on Ctrl+C.
- W06: reject native Windows invocation and enforce the Linux managed-PID boundary.
- W07: log environment and thermal provenance without mixing measured and modeled values.
- W08: reproduce the frozen project from a clean WSL checkout.

W03 and W05 require a real team WSL environment after capability probes. A skipped hardware check is not a passing test.
