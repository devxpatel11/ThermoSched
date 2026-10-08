from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from thermosched.config import SchedulerConfig, SimulationConfig, load_config
from thermosched.models import (
    CoreSample,
    DecisionAction,
    ProcessSample,
    ThermalKind,
)
from thermosched.scheduler.policy import PolicyState, evaluate_policy
from thermosched.sensors import (
    SensorInventory,
    SensorReading,
    SimulatedThermalSensor,
    ThermalSensorUnavailableError,
    select_thermal_backend,
)


def config_for_simulation(simulation: SimulationConfig) -> SchedulerConfig:
    return replace(SchedulerConfig(), simulation=simulation)


def test_simulator_heats_assigned_guest_cpu_from_utilization() -> None:
    config = config_for_simulation(
        SimulationConfig(ambient_c=20.0, heat_gain_per_s=10.0, cooling_per_s=2.0, throttle_temp_c=60.0)
    )
    simulator = SimulatedThermalSensor((2, 7), config)
    simulator.sample(0.0, utilization_by_cpu={2: 100.0, 7: 0.0}, assigned_cpu=2)

    snapshot = simulator.sample(1.0, utilization_by_cpu={2: 100.0, 7: 0.0}, assigned_cpu=2)

    assert snapshot.core(2).thermal_value == 30.0  # type: ignore[union-attr]
    assert snapshot.core(7).thermal_value == 20.0  # type: ignore[union-attr]
    assert snapshot.core(2).thermal_kind is ThermalKind.SIMULATED_C  # type: ignore[union-attr]
    assert snapshot.core(2).risk > snapshot.core(7).risk  # type: ignore[union-attr]


def test_duty_cycle_and_pause_reduce_assigned_workload_heat() -> None:
    simulation = SimulationConfig(
        ambient_c=20.0,
        heat_gain_per_s=10.0,
        cooling_per_s=0.0,
        throttle_temp_c=60.0,
        initial_temps_c=(30.0, 30.0),
    )
    config = config_for_simulation(simulation)
    full = SimulatedThermalSensor((2, 7), config)
    paced = SimulatedThermalSensor((2, 7), config)
    utilization = {2: 100.0, 7: 100.0}
    full.sample(0.0, utilization_by_cpu=utilization, assigned_cpu=2)
    paced.sample(0.0, utilization_by_cpu=utilization, assigned_cpu=2)

    full_snapshot = full.sample(1.0, utilization_by_cpu=utilization, assigned_cpu=2)
    paced_snapshot = paced.sample(1.0, utilization_by_cpu=utilization, assigned_cpu=2, duty_cycle=0.25)

    assert full_snapshot.core(2).thermal_value == 40.0  # type: ignore[union-attr]
    assert paced_snapshot.core(2).thermal_value == 32.5  # type: ignore[union-attr]
    paused = paced.sample(2.0, utilization_by_cpu=utilization, assigned_cpu=2, paused=True)
    assert paused.core(2).utilization_pct == 0.0  # type: ignore[union-attr]


def test_model_cools_to_ambient_and_clamps_at_configured_bounds() -> None:
    config = config_for_simulation(
        SimulationConfig(
            ambient_c=20.0,
            heat_gain_per_s=10.0,
            cooling_per_s=3.0,
            throttle_temp_c=25.0,
            initial_temps_c=(25.0,),
        )
    )
    simulator = SimulatedThermalSensor((7,), config)
    hot = simulator.sample(0.0, assigned_cpu=7)
    ceiling = simulator.sample(10.0, assigned_cpu=7)
    cool = simulator.sample(20.0)

    assert hot.core(7).thermal_value == 25.0  # type: ignore[union-attr]
    assert ceiling.core(7).thermal_value == 25.0  # type: ignore[union-attr]
    assert cool.core(7).thermal_value == 20.0  # type: ignore[union-attr]


def test_initial_state_is_repeatable_for_same_seed_and_changes_for_another_seed() -> None:
    config = config_for_simulation(
        SimulationConfig(
            ambient_c=20.0,
            heat_gain_per_s=1.0,
            cooling_per_s=1.0,
            throttle_temp_c=25.0,
            initial_jitter_c=2.0,
        )
    )
    first = SimulatedThermalSensor((2, 7, 11), config, seed=19).sample(0.0)
    repeated = SimulatedThermalSensor((2, 7, 11), config, seed=19).sample(0.0)
    other = SimulatedThermalSensor((2, 7, 11), config, seed=20).sample(0.0)

    assert tuple(core.thermal_value for core in first.cores) == tuple(core.thermal_value for core in repeated.cores)
    assert tuple(core.thermal_value for core in first.cores) != tuple(core.thermal_value for core in other.cores)


def test_reset_restores_seeded_initial_state_and_replay_time() -> None:
    config = config_for_simulation(
        SimulationConfig(
            ambient_c=20.0,
            heat_gain_per_s=5.0,
            cooling_per_s=2.0,
            throttle_temp_c=30.0,
            initial_jitter_c=1.0,
            seed=9,
        )
    )
    simulator = SimulatedThermalSensor((2, 7), config)
    first = simulator.sample(0.0)
    simulator.sample(2.0, assigned_cpu=2)

    simulator.reset()
    replay = simulator.sample(0.0)

    assert replay == first


def test_model_cpu_positions_map_to_non_contiguous_guest_cpu_ids() -> None:
    config = config_for_simulation(
        SimulationConfig(ambient_c=20.0, heat_gain_per_s=1.0, cooling_per_s=1.0, throttle_temp_c=30.0, initial_temps_c=(26, 22))
    )
    simulator = SimulatedThermalSensor((2, 7), config)

    snapshot = simulator.sample(4.0)

    assert [core.cpu_id for core in snapshot.cores] == [2, 7]
    assert [core.thermal_value for core in snapshot.cores] == [26.0, 22.0]
    with pytest.raises(ValueError, match="eligible guest CPU ID"):
        simulator.sample(5.0, assigned_cpu=0)


def test_repeatable_seeded_demo_fixtures_produce_expected_policy_actions() -> None:
    root = Path(__file__).parents[1]
    cases = (
        ("demo_migration.yaml", DecisionAction.MIGRATE, 7),
        ("demo_all_hot.yaml", DecisionAction.PACE, None),
    )
    for filename, expected_action, expected_cpu in cases:
        config = load_config(root / "config" / filename)
        process = ProcessSample(9001, 90.0, (2, 7), 2, True)
        decisions = []
        for _ in range(2):
            simulator = SimulatedThermalSensor((2, 7), config)
            snapshot = simulator.sample(0.0, utilization_by_cpu={2: 100.0, 7: 50.0}, assigned_cpu=2)
            result = evaluate_policy(
                snapshot,
                process,
                PolicyState(observed_cpu=2, residency_started_s=0.0),
                config,
                now_s=0.5,
                eligible_guest_cpus=(2, 7),
            )
            decisions.append((result.decision.action, result.decision.destination_cpu))
        assert decisions == [(expected_action, expected_cpu), (expected_action, expected_cpu)]


def test_explicit_simulate_needs_no_sensor_inventory() -> None:
    selection = select_thermal_backend("simulate", (2, 7), SchedulerConfig())

    frame = selection.sample_frame(0.0)

    assert selection.name == "simulate"
    assert frame.measured_readings == ()
    assert all(core.thermal_kind is ThermalKind.SIMULATED_C for core in frame.core_snapshot.cores)


def test_auto_mode_preserves_package_measurement_and_emits_derived_core_risk() -> None:
    package = SensorReading("/sys/class/hwmon/hwmon0/temp1_input", "chip: Package", "package", 72.0, "available")
    inventory = SensorInventory((package,))
    selection = select_thermal_backend("auto", (2, 7), SchedulerConfig(), sensor_inventory=inventory)

    frame = selection.sample_frame(0.0, utilization_by_cpu={2: 80.0, 7: 20.0}, assigned_cpu=2)

    assert selection.name == "auto-sensor-derived-risk"
    assert frame.measured_readings == (package,)
    assert all(core.thermal_kind is ThermalKind.RISK_ONLY for core in frame.core_snapshot.cores)
    assert frame.core_snapshot.core(2).thermal_value == pytest.approx((72.0 - 60.0) / (82.0 - 60.0))  # type: ignore[union-attr]
    assert frame.core_snapshot.core(2).thermal_value != 72.0  # type: ignore[union-attr]
    assert frame.core_snapshot.core(2).risk > frame.core_snapshot.core(7).risk  # type: ignore[union-attr]


def test_real_mode_uses_measured_source_as_evidence_and_derives_core_risk() -> None:
    reading = SensorReading("/sys/class/thermal/thermal_zone0/temp", "pkg", "package", 70.0, "available")
    selection = select_thermal_backend(
        "real", (2, 7), SchedulerConfig(), sensor_inventory=SensorInventory((reading,))
    )

    frame = selection.sample_frame(1.0)

    assert selection.name == "real-sensor-derived-risk"
    assert frame.measured_readings == (reading,)
    assert all(core.thermal_kind is ThermalKind.RISK_ONLY for core in frame.core_snapshot.cores)


def test_auto_falls_back_to_simulation_and_real_mode_fails_helpfully() -> None:
    inventory = SensorInventory(())

    selection = select_thermal_backend("auto", (2,), SchedulerConfig(), sensor_inventory=inventory)
    frame = selection.sample_frame(0.0)

    assert selection.name == "auto-simulate-fallback"
    assert frame.core_snapshot.cores[0].thermal_kind is ThermalKind.SIMULATED_C
    with pytest.raises(ThermalSensorUnavailableError, match="real mode requested"):
        select_thermal_backend("real", (2,), SchedulerConfig(), sensor_inventory=inventory)


def test_unknown_sensor_scope_is_not_treated_as_meaningful_in_auto_mode() -> None:
    unknown = SensorReading("/sys/class/hwmon/hwmon0/temp1_input", "chip: temp1", "unknown", 65.0, "available")
    selection = select_thermal_backend(
        "auto",
        (2,),
        SchedulerConfig(),
        sensor_inventory=SensorInventory((unknown,)),
    )

    frame = selection.sample_frame(0.0)

    assert selection.name == "auto-simulate-fallback"
    assert frame.measured_readings == ()
    assert frame.core_snapshot.cores[0].thermal_kind is ThermalKind.SIMULATED_C
