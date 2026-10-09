# Test Matrix - Task D1-C1

| Scenario ID | Workload Script | Config File | Policy evidence | A4 integration evidence |
| :--- | :--- | :--- | :--- | :--- |
| **TC-01** | `cpu_burn.py` | `demo_migration.yaml` | Fixed-input policy requests migration from model CPU 0 to a mapped safer guest CPU. | Live WSL controller applied migration, matched requested/readback masks, and restored the full original mask. |
| **TC-02** | `cpu_burn.py` | `demo_all_hot.yaml` | Fixed-input policy requests a bounded 200 ms micro-break when all candidates are hot. | Live WSL controller paced and resumed only its registered child, then restored its full mask. |
| **TC-03** | `cpu_burn.py` | `config/default.yaml` | WSL with no sensor inputs selects `auto-simulate-fallback`; explicit real mode reports unsupported. | Controller events retain `simulated_c`, backend, environment, guest CPU scope, model mapping, and requested/applied action. |

The workload scripts use monotonic wall-clock time. A seed does not make their live utilization or iteration counts deterministic.
