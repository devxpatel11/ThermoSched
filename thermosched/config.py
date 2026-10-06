"""Validated scheduler configuration loaded from YAML."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when a configuration file cannot produce a safe configuration."""


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{name} must be finite")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer")
    return value


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{name} must be a mapping with string keys")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"unknown {name} key(s): {', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class RiskWeights:
    thermal: float = 0.55
    utilization: float = 0.30
    trend: float = 0.15

    def __post_init__(self) -> None:
        values = (self.thermal, self.utilization, self.trend)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ConfigError("risk weights must be finite and non-negative")
        if not math.isclose(sum(values), 1.0, abs_tol=1e-9):
            raise ConfigError("risk weights must sum to 1.0")


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    ambient_c: float = 42.0
    heat_gain_per_s: float = 2.0
    cooling_per_s: float = 1.25
    throttle_temp_c: float = 85.0

    def __post_init__(self) -> None:
        values = (
            self.ambient_c,
            self.heat_gain_per_s,
            self.cooling_per_s,
            self.throttle_temp_c,
        )
        if any(not math.isfinite(value) for value in values):
            raise ConfigError("simulation values must be finite")
        if self.heat_gain_per_s < 0 or self.cooling_per_s < 0:
            raise ConfigError("simulation heating and cooling rates must be non-negative")
        if self.throttle_temp_c <= self.ambient_c:
            raise ConfigError("simulation throttle_temp_c must exceed ambient_c")


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    sampling_interval_s: float = 0.5
    warm_temp_c: float = 60.0
    high_temp_c: float = 75.0
    critical_temp_c: float = 82.0
    hysteresis_c: float = 3.0
    confirmation_samples: int = 2
    min_residency_s: float = 2.0
    migration_cooldown_s: float = 1.5
    min_destination_improvement: float = 0.15
    microbreak_ms: int = 150
    max_microbreak_ms: int = 250
    high_risk: float = 0.75
    critical_risk: float = 0.90
    risk_hysteresis: float = 0.05
    trend_scale_c_per_s: float = 5.0
    risk_weights: RiskWeights = RiskWeights()
    simulation: SimulationConfig = SimulationConfig()

    def __post_init__(self) -> None:
        finite_values = (
            self.sampling_interval_s,
            self.warm_temp_c,
            self.high_temp_c,
            self.critical_temp_c,
            self.hysteresis_c,
            self.min_residency_s,
            self.migration_cooldown_s,
            self.min_destination_improvement,
            self.high_risk,
            self.critical_risk,
            self.risk_hysteresis,
            self.trend_scale_c_per_s,
        )
        if any(not math.isfinite(value) for value in finite_values):
            raise ConfigError("scheduler values must be finite")
        if self.sampling_interval_s <= 0 or self.trend_scale_c_per_s <= 0:
            raise ConfigError("sampling_interval_s and trend_scale_c_per_s must be positive")
        if not self.warm_temp_c < self.high_temp_c < self.critical_temp_c:
            raise ConfigError("temperature thresholds must satisfy warm < high < critical")
        if self.hysteresis_c < 0 or self.hysteresis_c >= self.high_temp_c - self.warm_temp_c:
            raise ConfigError("hysteresis_c is outside the usable threshold range")
        if self.confirmation_samples < 1:
            raise ConfigError("confirmation_samples must be at least 1")
        if self.min_residency_s < 0 or self.migration_cooldown_s < 0:
            raise ConfigError("residency and cooldown values must be non-negative")
        if not 0 <= self.min_destination_improvement <= 1:
            raise ConfigError("min_destination_improvement must be between 0 and 1")
        if not 0 <= self.high_risk < self.critical_risk <= 1:
            raise ConfigError("risk thresholds must satisfy 0 <= high < critical <= 1")
        if not 0 <= self.risk_hysteresis < self.high_risk:
            raise ConfigError("risk_hysteresis must be non-negative and below high_risk")
        if self.microbreak_ms <= 0 or self.max_microbreak_ms <= 0:
            raise ConfigError("microbreak durations must be positive")
        if self.microbreak_ms > self.max_microbreak_ms:
            raise ConfigError("microbreak_ms cannot exceed max_microbreak_ms")
        if self.max_microbreak_ms > 1000:
            raise ConfigError("max_microbreak_ms cannot exceed 1000 ms")


def _risk_weights(data: Any) -> RiskWeights:
    values = _mapping(data, "risk_weights")
    allowed = {field.name for field in fields(RiskWeights)}
    _reject_unknown(values, allowed, "risk_weights")
    return RiskWeights(**{key: _number(value, f"risk_weights.{key}") for key, value in values.items()})


def _simulation(data: Any) -> SimulationConfig:
    values = _mapping(data, "simulation")
    allowed = {field.name for field in fields(SimulationConfig)}
    _reject_unknown(values, allowed, "simulation")
    return SimulationConfig(**{key: _number(value, f"simulation.{key}") for key, value in values.items()})


def config_from_mapping(data: dict[str, Any]) -> SchedulerConfig:
    values = _mapping(data, "configuration")
    allowed = {field.name for field in fields(SchedulerConfig)}
    _reject_unknown(values, allowed, "configuration")
    converted: dict[str, Any] = {}
    integer_fields = {"confirmation_samples", "microbreak_ms", "max_microbreak_ms"}
    for key, value in values.items():
        if key == "risk_weights":
            converted[key] = _risk_weights(value)
        elif key == "simulation":
            converted[key] = _simulation(value)
        elif key in integer_fields:
            converted[key] = _integer(value, key)
        else:
            converted[key] = _number(value, key)
    return SchedulerConfig(**converted)


def load_config(path: str | Path | None = None) -> SchedulerConfig:
    """Load a YAML file or return validated built-in defaults when path is omitted."""

    if path is None:
        return SchedulerConfig()
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return config_from_mapping({} if raw is None else raw)
    except (OSError, yaml.YAMLError, ConfigError, TypeError, ValueError) as exc:
        logger.error("failed to load configuration from %s: %s", config_path, exc)
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"failed to load configuration from {config_path}: {exc}") from exc
