from __future__ import annotations

import pytest

from thermosched.models import (
    CoreSample,
    CpuIdMap,
    Decision,
    DecisionAction,
    ProcessSample,
    ReplayFrame,
    ThermalKind,
    ThermalSnapshot,
)


def test_snapshot_preserves_explicit_simulated_provenance() -> None:
    core = CoreSample(3, 50.0, 67.5, ThermalKind.SIMULATED_C, 0.25, 0.4)
    snapshot = ThermalSnapshot(1.0, (core,), "simulation:model-v1")

    assert snapshot.core(3) == core
    assert snapshot.core(2) is None
    assert snapshot.cores[0].thermal_kind is ThermalKind.SIMULATED_C


def test_measured_sample_cannot_hide_a_missing_value() -> None:
    with pytest.raises(ValueError, match="require thermal_value"):
        CoreSample(0, 0.0, None, ThermalKind.MEASURED_C, 0.0)


def test_risk_only_value_is_normalized() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        CoreSample(0, 25.0, 70.0, ThermalKind.RISK_ONLY, 0.0)


def test_string_contract_values_are_validated_and_normalized() -> None:
    core = CoreSample(0, 25.0, 0.2, "risk_only", 0.0)  # type: ignore[arg-type]
    decision = Decision("stay", None, "below_threshold")  # type: ignore[arg-type]

    assert core.thermal_kind is ThermalKind.RISK_ONLY
    assert decision.action is DecisionAction.STAY


def test_cpu_mapping_handles_non_contiguous_guest_ids() -> None:
    mapping = CpuIdMap.from_eligible_guest_cpus([2, 7, 11])

    assert mapping.guest_cpu(0) == 2
    assert mapping.guest_cpu(2) == 11
    assert mapping.eligible_guest_cpus == (2, 7, 11)


def test_cpu_mapping_rejects_unknown_model_id() -> None:
    mapping = CpuIdMap.from_eligible_guest_cpus([2])

    with pytest.raises(ValueError, match="no eligible guest mapping"):
        mapping.guest_cpu(1)


@pytest.mark.parametrize(
    "decision",
    [
        Decision(DecisionAction.STAY, None, "below_threshold"),
        Decision(DecisionAction.MIGRATE, 3, "safer_destination"),
        Decision(DecisionAction.PACE, None, "all_candidates_unsafe", 150),
    ],
)
def test_valid_decision_contracts(decision: Decision) -> None:
    assert decision.reason


def test_replay_frame_keeps_fixed_time_and_inputs() -> None:
    thermal = ThermalSnapshot(
        10.0,
        (CoreSample(2, 80.0, 76.0, ThermalKind.SIMULATED_C, 1.0),),
        "simulation:fixture",
    )
    process = ProcessSample(999, 90.0, (2,), 2, True)

    replay = ReplayFrame(10.0, thermal, process)

    assert replay.at_s == replay.thermal.sampled_at_s
