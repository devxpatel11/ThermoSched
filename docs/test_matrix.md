# Test Matrix - Task D1-C1

| Scenario ID | Workload Script | Config File | Description | Target Result |
| :--- | :--- | :--- | :--- | :--- |
| **TC-01** | `cpu_burn.py` | `demo_migration.yaml` | Primary CPU overheats under continuous load. | Triggers CPU affinity migration to safe candidate CPU. |
| **TC-02** | `bursty.py` | `demo_all_hot.yaml` | All candidate CPUs exceed thermal threshold. | Triggers micro-break pacing mitigation. |
| **TC-03** | `cpu_burn.py` | None | Fallback test when sensor readings are missing. | Gracefully defaults or logs safety warnings. |
