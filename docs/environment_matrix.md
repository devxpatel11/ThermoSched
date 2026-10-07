# Environment and capability matrix

This is an evidence record, not a declaration that planned capabilities work. Each member updates only their row from Ubuntu WSL 2. Use `N/A`, `failed: <reason>`, or `skipped: <reason>` where appropriate.

## E0 environment record

| Member | Date | Windows version | Distribution | WSL version | Linux kernel | Python | psutil | Eligible guest CPU IDs | Logical guest CPUs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 2026-10-07 | Windows 11 Home | Ubuntu 26.04.1 LTS | 2 | 6.18.40.1-microsoft-standard-WSL2 | 3.14.4 | 7.2.2 | 0-19 | 20 |
| B | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| C | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## Capability and sensor evidence

| Member/machine | Sensor paths and provenance | Simulation selected | Child affinity set/readback/restore | Child pause/resume/restore | Live migration eligible | Notes or skip reason |
| --- | --- | --- | --- | --- | --- | --- |
| A | No host thermal sensor used; `simulated_c` assignment-coupled model | yes | passed: disposable child set/readback/full-mask restore | passed: bounded SIGSTOP/SIGCONT and SIGINT/SIGTERM restore | yes: 20 eligible CPUs, verified 0 to 1 migration | 300-second live soak: 3,000 samples, 0 failures, restored; no root; guest movement does not prove physical host-core movement |
| B | pending | pending | pending | pending | pending | pending |
| C | pending | pending | pending | pending | pending | pending |

At least one demonstration machine must have two eligible guest CPUs and passing child-affinity probes for live migration evidence. A one-CPU machine may run pure policy fixtures with a mocked actuator and real bounded pacing only when its pause/resume probe passes.
