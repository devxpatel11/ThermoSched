"""Policy-agnostic actuation boundary and managed-child safety rules."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from thermosched.models import ActuatorCapabilities, ManagedProcess


class SafetyViolation(RuntimeError):
    """Raised before an operation could target an unsafe process or CPU."""


class ManagedPidRegistry:
    """Issues managed targets only for child PIDs explicitly registered by a launcher."""

    def __init__(self, controller_pid: int | None = None) -> None:
        self._controller_pid = os.getpid() if controller_pid is None else controller_pid
        self._protected_pids = {0, 1, self._controller_pid, os.getppid()}
        self._targets: dict[int, ManagedProcess] = {}

    def register_child(
        self,
        pid: int,
        original_affinity: tuple[int, ...] | list[int],
        eligible_guest_cpus: tuple[int, ...] | list[int] | None = None,
    ) -> ManagedProcess:
        if pid in self._protected_pids or pid <= 1:
            raise SafetyViolation(f"PID {pid} is protected and cannot be managed")
        if pid in self._targets:
            raise SafetyViolation(f"PID {pid} is already registered")
        original = tuple(original_affinity)
        eligible = original if eligible_guest_cpus is None else tuple(eligible_guest_cpus)
        try:
            target = ManagedProcess(pid, original, eligible)
        except ValueError as exc:
            raise SafetyViolation(str(exc)) from exc
        self._targets[pid] = target
        return target

    def require_managed(self, pid: int) -> ManagedProcess:
        try:
            return self._targets[pid]
        except KeyError as exc:
            raise SafetyViolation(f"PID {pid} is not a registered managed child") from exc

    def unregister(self, pid: int) -> ManagedProcess:
        target = self.require_managed(pid)
        del self._targets[pid]
        return target


def validate_requested_cpus(target: ManagedProcess, requested_cpus: tuple[int, ...] | list[int]) -> tuple[int, ...]:
    requested = tuple(requested_cpus)
    if not requested:
        raise SafetyViolation("requested affinity must not be empty")
    invalid = sorted(set(requested) - set(target.eligible_guest_cpus))
    if invalid:
        raise SafetyViolation(f"requested CPU IDs are outside the eligible guest mask: {invalid}")
    return requested


class Actuator(ABC):
    """OS control mechanism; implementations must not make policy decisions."""

    @abstractmethod
    def capabilities(self) -> ActuatorCapabilities:
        """Report probed capabilities and reasons for unavailable controls."""

    @abstractmethod
    def set_affinity(self, target: ManagedProcess, cpu_ids: tuple[int, ...]) -> tuple[int, ...]:
        """Apply and return the observed guest affinity mask."""

    @abstractmethod
    def pause(self, target: ManagedProcess) -> None:
        """Pause a registered managed process."""

    @abstractmethod
    def resume(self, target: ManagedProcess) -> None:
        """Resume a registered managed process."""

    @abstractmethod
    def restore(self, target: ManagedProcess) -> None:
        """Resume if needed and restore the original affinity."""
