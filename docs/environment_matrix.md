# Environment and capability matrix

This is an evidence record, not a declaration that planned capabilities work. Each member updates only their row from Ubuntu WSL 2. Use `N/A`, `failed: <reason>`, or `skipped: <reason>` where appropriate.

## E0 environment record

| Member | Date | Windows version | Distribution | WSL version | Linux kernel | Python | psutil | Eligible guest CPU IDs | Logical guest CPUs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| B | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| C | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## Capability and sensor evidence

| Member/machine | Sensor paths and provenance | Simulation selected | Child affinity set/readback/restore | Child pause/resume/restore | Live migration eligible | Notes or skip reason |
| --- | --- | --- | --- | --- | --- | --- |
| A | pending | pending | pending | pending | pending | pending |
| B | pending | pending | pending | pending | pending | pending |
| C | pending | pending | pending | pending | pending | pending |

At least one demonstration machine must have two eligible guest CPUs and passing child-affinity probes for live migration evidence. A one-CPU machine may run pure policy fixtures with a mocked actuator and real bounded pacing only when its pause/resume probe passes.
