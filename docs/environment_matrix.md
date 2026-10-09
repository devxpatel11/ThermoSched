# Environment and capability matrix

This is an evidence record, not a declaration that planned capabilities work. Each member updates only their row from Ubuntu WSL 2. Use `N/A`, `failed: <reason>`, or `skipped: <reason>` where appropriate.

## E0 environment record

| Member | Date | Windows version | Distribution | WSL version | Linux kernel | Python | psutil | Eligible guest CPU IDs | Logical guest CPUs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | 2026-10-07 | Windows 11 Home | Ubuntu 26.04.1 LTS | 2 | 6.18.40.1-microsoft-standard-WSL2 | 3.14.4 | 7.2.2 | 0-19 | 20 |
| B | 2026-10-08 | Windows 11 Home | Ubuntu 26.04.1 LTS | 2 | 6.18.40.1-microsoft-standard-WSL2 | 3.14.4 | 7.2.2 | 0-19 | 20 |
| C | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## Capability and sensor evidence

| Member/machine | Sensor paths and provenance | Simulation selected | Child affinity set/readback/restore | Child pause/resume/restore | Live migration eligible | Notes or skip reason |
| --- | --- | --- | --- | --- | --- | --- |
| A | no `/sys` temperature inputs exposed; `simulated_c` used without a host-temperature claim | passed: B3 `simulate` backend | passed: disposable child set/readback/full-mask restore | passed: bounded 200 ms SIGSTOP/SIGCONT plus SIGINT/SIGTERM restore | yes: 20 eligible CPUs; verified migration with exact readback | 2026-10-09 A/B/C pipeline: migration and all-hot demos had 0 failures and restored all 20 CPUs; fixed-clock 300-second soak passed |
| B | no `/sys` temperature inputs exposed; unavailable reported without fabricated data | passed: `auto-simulate-fallback`; deterministic 300-second fixed-clock soak | N/A: B1-B3 are read-only | N/A: B1-B3 are read-only | capability only: 20 eligible guest CPUs; actuation not tested by B1-B3 | Live B1→B2→B3 integration passed; real mode unsupported without sensors; guest CPUs are not Windows physical cores |
| C | pending | pending | pending | pending | pending | pending |

At least one demonstration machine must have two eligible guest CPUs and passing child-affinity probes for live migration evidence. A one-CPU machine may run pure policy fixtures with a mocked actuator and real bounded pacing only when its pause/resume probe passes.
