import sys
from typing import Dict, Any, List


class TerminalDashboard:
    """Simple terminal dashboard display for real-time thermal telemetry."""

    def __init__(self, num_cpus: int = 4):
        self.num_cpus = num_cpus

    def render(self, cpu_stats: List[Dict[str, Any]], active_mode: str = "simulate") -> None:
        output = [
            f"\033[H\033[J",  # Clear screen ansi escape
            f"=== ThermoSched Active Controller ({active_mode.upper()}) ===",
            "-" * 50,
            f"{'CPU':<6} | {'Temp (°C)':<10} | {'Risk Score':<12} | {'Status':<10}",
            "-" * 50,
        ]

        for stat in cpu_stats:
            cpu_id = stat.get("cpu_id", 0)
            temp = stat.get("temp", 0.0)
            risk = stat.get("risk", 0.0)
            status = stat.get("status", "NORMAL")
            output.append(f"CPU {cpu_id:<2} | {temp:<10.1f} | {risk:<12.2f} | {status:<10}")

        output.append("-" * 50)
        output.append("Press Ctrl+C to terminate pacing run.")
        print("\n".join(output))
