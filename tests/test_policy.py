from __future__ import annotations

from dataclasses import replace

import pytest

from thermosched.config import SchedulerConfig
from thermosched.models import (
    CoreSample,
    DecisionAction,
    ProcessSample,
    ThermalKind,
    ThermalSnapshot,
)
from thermosched.scheduler.heat import risk_score
from thermosched.scheduler.policy import PolicyState, evaluate_policy, record_migration


def core(
    cpu_id: int,
    temp_c: float,
    utilization: float = 50.0,
    trend: float = 0.0,
) -> CoreSample:
    return CoreSample(cpu_id, utilization, temp_c, ThermalKind.SIMULATED_C, trend)


def snapshot(*cores: CoreSample, at_s: float = 10.0) -> ThermalSnapshot:
    return ThermalSnapshot(at_s, cores, "simulation:test-fixture")


def process(current_cpu: int | None, affinity: tuple[int, ...], alive: bool = True) -> ProcessSample:
    return ProcessSample(9001, 90.0, affinity, current_cpu, alive)


def immediate_config(**changes: object) -> SchedulerConfig:
    defaults: dict[str, object] = {
        "confirmation_samples": 1,
        "min_residency_s": 0.0,
        "migration_cooldown_s": 0.0,
    }
    defaults.update(changes)
    return replace(SchedulerConfig(), **defaults)


@pytest.mark.parametrize(
    ("thermal", "proc", "eligible", "expected_action", "expected_reason"),
    [
        (
            snapshot(core(2, 65.0), core(7, 55.0)),
            process(2, (2, 7)),
            (2, 7),
            DecisionAction.STAY,
            "below_threshold",
        ),
        (
            snapshot(core(2, 80.0, 100.0, 2.0), core(7, 55.0, 10.0)),
            process(2, (2, 7)),
            (2, 7),
            DecisionAction.MIGRATE,
            "safer_destination",
        ),
        (
            snapshot(core(2, 80.0), core(7, 79.0)),
            process(2, (2, 7)),
            (2, 7),
            DecisionAction.PACE,
            "all_candidates_unsafe",
        ),
        (
            snapshot(core(2, 80.0)),
            process(2, (2,)),
            (2,),
            DecisionAction.PACE,
            "all_candidates_unsafe",
        ),
        (
            snapshot(core(2, 80.0), core(7, 55.0)),
            process(2, (2, 7)),
            (),
            DecisionAction.STAY,
            "invalid_eligible_mask",
        ),
        (
            snapshot(core(2, 80.0), core(7, 55.0)),
            process(2, (2, 7)),
            (2, -1),
            DecisionAction.STAY,
            "invalid_eligible_mask",
        ),
        (
            snapshot(core(2, 80.0), core(7, 55.0)),
            process(2, (2, 7)),
            (2, 2),
            DecisionAction.STAY,
            "invalid_eligible_mask",
        ),
    ],
)
def test_policy_decision_table(
    thermal: ThermalSnapshot,
    proc: ProcessSample,
    eligible: tuple[int, ...],
    expected_action: DecisionAction,
    expected_reason: str,
) -> None:
    result = evaluate_policy(
        thermal,
        proc,
        PolicyState(),
        immediate_config(),
        now_s=10.0,
        eligible_guest_cpus=eligible,
    )

    assert result.decision.action is expected_action
    assert result.decision.reason == expected_reason
    if result.decision.destination_cpu is not None:
        assert result.decision.destination_cpu in eligible


def test_non_contiguous_mask_and_tie_choose_lowest_guest_cpu_id() -> None:
    result = evaluate_policy(
        snapshot(core(11, 80.0, 100.0), core(7, 55.0, 10.0), core(2, 55.0, 10.0)),
        process(11, (2, 7, 11)),
        PolicyState(),
        immediate_config(),
        now_s=10.0,
        eligible_guest_cpus=(2, 7, 11),
    )

    assert result.decision.action is DecisionAction.MIGRATE
    assert result.decision.destination_cpu == 2


def test_missing_candidate_telemetry_degrades_to_stay() -> None:
    result = evaluate_policy(
        snapshot(core(2, 80.0)),
        process(2, (2, 7)),
        PolicyState(),
        immediate_config(),
        now_s=10.0,
        eligible_guest_cpus=(2, 7),
    )

    assert result.decision.action is DecisionAction.STAY
    assert result.decision.reason == "candidate_telemetry_missing"


def test_hysteresis_keeps_hot_state_until_cool_margin_is_crossed() -> None:
    config = immediate_config(confirmation_samples=2)
    first = evaluate_policy(
        snapshot(core(2, 76.0, 80.0), core(7, 55.0, 10.0)),
        process(2, (2, 7)),
        PolicyState(),
        config,
        now_s=10.0,
        eligible_guest_cpus=(2, 7),
    )
    inside_margin = evaluate_policy(
        snapshot(core(2, 73.0, 80.0), core(7, 55.0, 10.0), at_s=10.5),
        process(2, (2, 7)),
        first.state,
        config,
        now_s=10.5,
        eligible_guest_cpus=(2, 7),
    )
    below_margin = evaluate_policy(
        snapshot(core(2, 71.0, 20.0), core(7, 55.0, 10.0), at_s=10.5),
        process(2, (2, 7)),
        first.state,
        config,
        now_s=10.5,
        eligible_guest_cpus=(2, 7),
    )

    assert first.decision.reason == "awaiting_hot_confirmation"
    assert inside_margin.decision.action is DecisionAction.MIGRATE
    assert below_margin.decision.reason == "hysteresis_cleared"
    assert below_margin.state.consecutive_hot_samples == 0


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        (PolicyState(observed_cpu=2, residency_started_s=9.0), "minimum_residency"),
        (
            PolicyState(observed_cpu=2, residency_started_s=0.0, last_migration_s=9.5),
            "migration_cooldown",
        ),
    ],
)
def test_migration_timing_gates(state: PolicyState, expected_reason: str) -> None:
    result = evaluate_policy(
        snapshot(core(2, 80.0, 100.0), core(7, 55.0, 10.0)),
        process(2, (2, 7)),
        state,
        SchedulerConfig(confirmation_samples=1),
        now_s=10.0,
        eligible_guest_cpus=(2, 7),
    )

    assert result.decision.action is DecisionAction.STAY
    assert result.decision.reason == expected_reason


def test_critical_temperature_bypasses_sample_confirmation() -> None:
    result = evaluate_policy(
        snapshot(core(2, 83.0, 100.0), core(7, 55.0, 10.0)),
        process(2, (2, 7)),
        PolicyState(observed_cpu=2, residency_started_s=0.0),
        immediate_config(confirmation_samples=3),
        now_s=10.0,
        eligible_guest_cpus=(2, 7),
    )

    assert result.decision.action is DecisionAction.MIGRATE


def test_insufficient_improvement_prevents_migration() -> None:
    result = evaluate_policy(
        snapshot(core(2, 75.0, 40.0), core(7, 74.0, 35.0)),
        process(2, (2, 7)),
        PolicyState(),
        immediate_config(min_destination_improvement=0.5),
        now_s=10.0,
        eligible_guest_cpus=(2, 7),
    )

    assert result.decision.reason == "insufficient_destination_improvement"


def test_policy_replay_is_repeatable_from_fixed_inputs() -> None:
    inputs = (
        snapshot(core(2, 80.0, 100.0, 2.0), core(7, 55.0, 10.0)),
        process(2, (2, 7)),
        PolicyState(observed_cpu=2, residency_started_s=0.0),
        immediate_config(),
    )

    first = evaluate_policy(*inputs, now_s=10.0, eligible_guest_cpus=(2, 7))
    second = evaluate_policy(*inputs, now_s=10.0, eligible_guest_cpus=(2, 7))

    assert first == second


def test_migration_state_changes_only_after_explicit_readback_confirmation() -> None:
    state = PolicyState(observed_cpu=2, residency_started_s=0.0)

    updated = record_migration(state, destination_cpu=7, applied_at_s=10.0)

    assert state.last_migration_s is None
    assert updated.observed_cpu == 7
    assert updated.last_migration_s == 10.0


def test_risk_score_uses_thermal_utilization_and_positive_trend() -> None:
    config = SchedulerConfig()
    rising = core(2, 71.0, 50.0, 2.5)
    cooling = core(2, 71.0, 50.0, -2.5)

    assert risk_score(rising, config) == pytest.approx(0.5)
    assert risk_score(cooling, config) == pytest.approx(0.425)
