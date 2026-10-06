"""Deterministic scheduling policy with no OS or timing side effects."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from thermosched.config import SchedulerConfig
from thermosched.models import (
    CoreSample,
    Decision,
    DecisionAction,
    ProcessSample,
    ThermalKind,
    ThermalSnapshot,
)
from thermosched.scheduler.heat import score_snapshot


@dataclass(frozen=True, slots=True)
class PolicyState:
    observed_cpu: int | None = None
    residency_started_s: float = 0.0
    last_migration_s: float | None = None
    consecutive_hot_samples: int = 0
    hot_latched: bool = False

    def __post_init__(self) -> None:
        if self.observed_cpu is not None and self.observed_cpu < 0:
            raise ValueError("observed_cpu must be non-negative")
        for name, value in (
            ("residency_started_s", self.residency_started_s),
            ("last_migration_s", self.last_migration_s),
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.consecutive_hot_samples < 0:
            raise ValueError("consecutive_hot_samples must be non-negative")


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: Decision
    state: PolicyState
    snapshot: ThermalSnapshot


def record_migration(state: PolicyState, destination_cpu: int, applied_at_s: float) -> PolicyState:
    """Update policy timing only after the controller verifies a real migration."""

    if destination_cpu < 0:
        raise ValueError("destination_cpu must be non-negative")
    if not math.isfinite(applied_at_s) or applied_at_s < 0:
        raise ValueError("applied_at_s must be finite and non-negative")
    return PolicyState(
        observed_cpu=destination_cpu,
        residency_started_s=applied_at_s,
        last_migration_s=applied_at_s,
    )


def _temperature_at_or_above(sample: CoreSample, threshold_c: float) -> bool:
    return (
        sample.thermal_kind is not ThermalKind.RISK_ONLY
        and sample.thermal_value is not None
        and sample.thermal_value >= threshold_c
    )


def _critical(sample: CoreSample, config: SchedulerConfig) -> bool:
    return _temperature_at_or_above(sample, config.critical_temp_c) or sample.risk >= config.critical_risk


def _hot(sample: CoreSample, config: SchedulerConfig, latched: bool) -> bool:
    temp_threshold = config.high_temp_c - (config.hysteresis_c if latched else 0.0)
    risk_threshold = config.high_risk - (config.risk_hysteresis if latched else 0.0)
    return _temperature_at_or_above(sample, temp_threshold) or sample.risk >= risk_threshold


def _stay(reason: str) -> Decision:
    return Decision(DecisionAction.STAY, None, reason)


def _with_decision(
    decision: Decision,
    state: PolicyState,
    snapshot: ThermalSnapshot,
) -> PolicyResult:
    return PolicyResult(decision, state, snapshot)


def evaluate_policy(
    snapshot: ThermalSnapshot,
    process: ProcessSample,
    state: PolicyState,
    config: SchedulerConfig,
    *,
    now_s: float,
    eligible_guest_cpus: tuple[int, ...] | list[int],
) -> PolicyResult:
    """Return one repeatable policy decision from explicit inputs."""

    scored = score_snapshot(snapshot, config)
    if not math.isfinite(now_s) or now_s < 0:
        return _with_decision(_stay("invalid_time"), state, scored)

    eligible = tuple(eligible_guest_cpus)
    if (
        not eligible
        or any(cpu < 0 for cpu in eligible)
        or len(eligible) != len(set(eligible))
    ):
        return _with_decision(_stay("invalid_eligible_mask"), state, scored)
    if not process.alive:
        return _with_decision(_stay("process_exited"), state, scored)
    if process.current_cpu is None:
        return _with_decision(_stay("current_cpu_unknown"), state, scored)
    if process.current_cpu not in eligible:
        return _with_decision(_stay("current_cpu_outside_eligible_mask"), state, scored)

    current_cpu = process.current_cpu
    current = scored.core(current_cpu)
    if current is None:
        return _with_decision(_stay("current_cpu_missing_from_snapshot"), state, scored)

    if state.observed_cpu != current_cpu:
        state = PolicyState(observed_cpu=current_cpu, residency_started_s=now_s)

    critical = _critical(current, config)
    hot = critical or _hot(current, config, state.hot_latched)
    next_state = replace(
        state,
        consecutive_hot_samples=(state.consecutive_hot_samples + 1 if hot else 0),
        hot_latched=hot,
    )
    if not hot:
        reason = "hysteresis_cleared" if state.hot_latched else "below_threshold"
        return _with_decision(_stay(reason), next_state, scored)
    if not critical and next_state.consecutive_hot_samples < config.confirmation_samples:
        return _with_decision(_stay("awaiting_hot_confirmation"), next_state, scored)

    other_cpu_ids = tuple(cpu for cpu in eligible if cpu != current_cpu)
    if not other_cpu_ids:
        decision = Decision(DecisionAction.PACE, None, "all_candidates_unsafe", config.microbreak_ms)
        return _with_decision(decision, next_state, scored)

    candidates = [scored.core(cpu) for cpu in other_cpu_ids]
    if any(candidate is None for candidate in candidates):
        return _with_decision(_stay("candidate_telemetry_missing"), next_state, scored)
    available = [candidate for candidate in candidates if candidate is not None]
    safe = [candidate for candidate in available if not _hot(candidate, config, False)]
    if not safe:
        decision = Decision(DecisionAction.PACE, None, "all_candidates_unsafe", config.microbreak_ms)
        return _with_decision(decision, next_state, scored)

    destination = min(safe, key=lambda sample: (sample.risk, sample.cpu_id))
    improvement = current.risk - destination.risk
    if improvement + 1e-12 < config.min_destination_improvement:
        return _with_decision(_stay("insufficient_destination_improvement"), next_state, scored)
    if now_s - next_state.residency_started_s < config.min_residency_s:
        return _with_decision(_stay("minimum_residency"), next_state, scored)
    if (
        next_state.last_migration_s is not None
        and now_s - next_state.last_migration_s < config.migration_cooldown_s
    ):
        return _with_decision(_stay("migration_cooldown"), next_state, scored)

    decision = Decision(DecisionAction.MIGRATE, destination.cpu_id, "safer_destination")
    return _with_decision(decision, next_state, scored)
