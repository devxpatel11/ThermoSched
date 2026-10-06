"""Pure thermal-risk scoring helpers."""

from __future__ import annotations

from dataclasses import replace

from thermosched.config import SchedulerConfig
from thermosched.models import CoreSample, ThermalKind, ThermalSnapshot


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def risk_score(sample: CoreSample, config: SchedulerConfig) -> float:
    """Calculate modeled risk without changing the sample or reading hardware."""

    if sample.thermal_kind is ThermalKind.RISK_ONLY:
        thermal = 0.0 if sample.thermal_value is None else sample.thermal_value
    else:
        thermal = clamp(
            (sample.thermal_value - config.warm_temp_c)
            / (config.critical_temp_c - config.warm_temp_c)
        )
    utilization = clamp(sample.utilization_pct / 100.0)
    trend = clamp(max(0.0, sample.trend_per_s) / config.trend_scale_c_per_s)
    weights = config.risk_weights
    return clamp(
        weights.thermal * thermal
        + weights.utilization * utilization
        + weights.trend * trend
    )


def score_snapshot(snapshot: ThermalSnapshot, config: SchedulerConfig) -> ThermalSnapshot:
    cores = tuple(replace(core, risk=risk_score(core, config)) for core in snapshot.cores)
    return replace(snapshot, cores=cores)
