from __future__ import annotations

import os

import pytest

from thermosched.scheduler.actuator import ManagedPidRegistry, SafetyViolation, validate_requested_cpus


def unused_pid() -> int:
    return max(os.getpid(), os.getppid(), 100) + 10_000


def test_registry_rejects_protected_and_unregistered_pids() -> None:
    registry = ManagedPidRegistry(controller_pid=100)

    with pytest.raises(SafetyViolation, match="protected"):
        registry.register_child(1, [2])
    with pytest.raises(SafetyViolation, match="protected"):
        registry.register_child(100, [2])
    with pytest.raises(SafetyViolation, match="not a registered managed child"):
        registry.require_managed(999)


def test_registry_returns_only_explicitly_registered_child() -> None:
    registry = ManagedPidRegistry(controller_pid=100)
    pid = unused_pid()

    target = registry.register_child(pid, [2, 7], [7])

    assert registry.require_managed(pid) is target
    assert registry.unregister(pid) is target
    with pytest.raises(SafetyViolation, match="not a registered managed child"):
        registry.require_managed(pid)


def test_requested_cpu_must_be_inside_original_eligible_mask() -> None:
    target = ManagedPidRegistry(controller_pid=100).register_child(unused_pid(), [2, 7], [2, 7])

    assert validate_requested_cpus(target, [7]) == (7,)
    with pytest.raises(SafetyViolation, match="outside the eligible guest mask"):
        validate_requested_cpus(target, [0])
    with pytest.raises(SafetyViolation, match="duplicate"):
        validate_requested_cpus(target, [2, 2])


def test_eligible_mask_must_be_within_original_affinity() -> None:
    registry = ManagedPidRegistry(controller_pid=100)

    with pytest.raises(SafetyViolation, match="within original affinity"):
        registry.register_child(unused_pid(), [2], [7])
