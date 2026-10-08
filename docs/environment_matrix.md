# Environment and capability matrix

This is an evidence record, not a declaration that planned capabilities work. Each member updates only their row from Ubuntu WSL 2. Use `N/A`, `failed: <reason>`, or `skipped: <reason>` where appropriate.

## E0 environment record

| Member | Date | Windows version | Distribution | WSL version | Linux kernel | Python | psutil | Eligible guest CPU IDs | Logical guest CPUs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| B | 2026-10-08 | Windows 11 Home | Ubuntu 26.04.1 LTS | 2 | 6.18.40.1-microsoft-standard-WSL2 | 3.14.4 | 7.2.2 | 0-19 | 20 |
| C | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## Capability and sensor evidence

| Member/machine | Sensor paths and provenance | Simulation selected | Child affinity set/readback/restore | Child pause/resume/restore | Live migration eligible | Notes or skip reason |
| --- | --- | --- | --- | --- | --- | --- |
| A | pending | pending | pending | pending | pending | pending |
| B | no `/sys` temperature inputs exposed; unavailable reported without fabricated data | yes: explicit fallback required; B3 backend pending | N/A: B1/B2 are read-only | N/A: B1/B2 are read-only | capability only: 20 eligible guest CPUs; actuation not tested by B1/B2 | Integration validation machine; WSL guest CPUs are not Windows physical cores |
| C | pending | pending | pending | pending | pending | pending |

At least one demonstration machine must have two eligible guest CPUs and passing child-affinity probes for live migration evidence. A one-CPU machine may run pure policy fixtures with a mocked actuator and real bounded pacing only when its pause/resume probe passes.
