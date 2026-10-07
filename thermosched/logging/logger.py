import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, List


class EventLogger:
    """Logs thermal pacing events to CSV and JSONL formats."""

    def __init__(self, log_dir: str = "logs", run_id: Optional[str] = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or f"run_{int(time.time())}"
        self.csv_path = self.log_dir / f"{self.run_id}.csv"
        self.jsonl_path = self.log_dir / f"{self.run_id}.jsonl"
        self._init_csv()

    def _init_csv(self) -> None:
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "event_type", "cpu_id",
                    "temp_measured", "temp_simulated", "risk_score", "action"
                ])

    def log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        timestamp = data.get("timestamp", time.time())
        
        # Write CSV row
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp,
                event_type,
                data.get("cpu_id", ""),
                data.get("temp_measured", ""),
                data.get("temp_simulated", ""),
                data.get("risk_score", ""),
                data.get("action", "")
            ])

        # Write JSONL record
        record = {"timestamp": timestamp, "event_type": event_type, **data}
        with open(self.jsonl_path, "a") as f:
            f.write(json.dumps(record) + "\n")
