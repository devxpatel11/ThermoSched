# Test Matrix - Task D1-C1

| Scenario ID | Workload Script | Config File | Current evidence | Final A4 target |
| :--- | :--- | :--- | :--- | :--- |
| **TC-01** | `cpu_burn.py` | `demo_migration.yaml` | Fixed-input policy test requests migration from model CPU 0 to a mapped safer guest CPU. | Live controller applies migration and verifies affinity readback. |
| **TC-02** | `bursty.py` | `demo_all_hot.yaml` | Fixed-input policy test requests a bounded 200 ms micro-break when all candidates are hot. | Live controller pauses and resumes only its managed child. |
| **TC-03** | `cpu_burn.py` | `config/default.yaml` | WSL with no sensor inputs selects `auto-simulate-fallback`; explicit real mode reports unsupported. | Controller logs the fallback provenance throughout the run. |

The workload scripts use monotonic wall-clock time. A seed does not make their live utilization or iteration counts deterministic.
