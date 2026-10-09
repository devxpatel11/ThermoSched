"""Terminal rendering for provenance-labelled thermal telemetry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _thermal_text(stat: Mapping[str, Any]) -> str:
    raw_provenance = stat.get("thermal_kind", stat.get("provenance", "unknown"))
    provenance = str(getattr(raw_provenance, "value", raw_provenance))
    value = stat.get("thermal_value", stat.get("temp"))
    if value is None:
        return f"N/A [{provenance}]"
    if provenance in {"measured_c", "simulated_c"}:
        return f"{float(value):.1f} C [{provenance}]"
    if provenance == "risk_only":
        return f"{float(value):.2f} [risk_only]"
    return f"{value} [{provenance}]"


class TerminalDashboard:
    """Render guest CPU state without presenting unknown values as Celsius."""

    def __init__(self, num_cpus: int = 4) -> None:
        if num_cpus < 1:
            raise ValueError("num_cpus must be positive")
        self.num_cpus = num_cpus

    def render(
        self,
        cpu_stats: Sequence[Mapping[str, Any]],
        active_mode: str = "simulate",
        context: Mapping[str, Any] | None = None,
    ) -> None:
        output = [
            "\033[H\033[J",
            f"=== ThermoSched ({active_mode.upper()}) ===",
        ]
        if context:
            output.append(
                f"environment={context.get('environment', 'unknown')} "
                f"eligible_guest_cpus={context.get('eligible_guest_cpus', ())} "
                f"paused={context.get('paused', False)}"
            )
        output.extend(
            (
                "-" * 78,
                f"{'Guest CPU':<10} | {'Thermal value and provenance':<35} | {'Risk':<8} | {'Status':<10}",
                "-" * 78,
            )
        )
        for stat in cpu_stats:
            output.append(
                f"{stat.get('cpu_id', '?')!s:<10} | {_thermal_text(stat):<35} | "
                f"{float(stat.get('risk', 0.0)):<8.2f} | {stat.get('status', 'UNKNOWN')!s:<10}"
            )
        output.extend(("-" * 78, "Press Ctrl+C to stop the managed run."))
        print("\n".join(output))
