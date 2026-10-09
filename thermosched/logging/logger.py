"""CSV and JSONL event logging with explicit thermal provenance."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any


CSV_FIELDS = (
    "timestamp",
    "event_type",
    "cpu_id",
    "temp_measured",
    "temp_simulated",
    "thermal_provenance",
    "risk_score",
    "backend",
    "eligible_guest_cpus",
    "requested_action",
    "applied_action",
)


class EventLogger:
    """Append controller events to one CSV file and one JSONL file."""

    def __init__(self, log_dir: str | Path = "logs", run_id: str | None = None) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or f"run_{time.time_ns()}"
        if Path(self.run_id).name != self.run_id or self.run_id in {"", ".", ".."}:
            raise ValueError("run_id must be a plain file name")
        self.csv_path = self.log_dir / f"{self.run_id}.csv"
        self.jsonl_path = self.log_dir / f"{self.run_id}.jsonl"
        if not self.csv_path.exists():
            with self.csv_path.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(CSV_FIELDS)

    def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must not be empty")
        timestamp = data.get("timestamp", time.time())
        row = {**data, "timestamp": timestamp, "event_type": event_type}
        with self.csv_path.open("a", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerow(row.get(field, "") for field in CSV_FIELDS)
        with self.jsonl_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
