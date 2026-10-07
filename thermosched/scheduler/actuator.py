"""Policy-agnostic Linux actuation and managed-child safety rules."""

from __future__ import annotations

import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
import logging
from types import FrameType

import psutil

from thermosched.models import ActuatorCapabilities, ManagedProcess


class SafetyViolation(RuntimeError):
    """Raised before an operation could target an unsafe process or CPU."""


class ActuationError(RuntimeError):
    """Raised when an approved child could not be controlled or verified."""


logger = logging.getLogger(__name__)


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
        *,
        create_time_s: float | None = None,
        owner_uid: int | None = None,
        parent_pid: int | None = None,
    ) -> ManagedProcess:
        if pid in self._protected_pids or pid <= 1:
            raise SafetyViolation(f"PID {pid} is protected and cannot be managed")
        if pid in self._targets:
            raise SafetyViolation(f"PID {pid} is already registered")
        original = tuple(original_affinity)
        eligible = original if eligible_guest_cpus is None else tuple(eligible_guest_cpus)
        try:
            target = ManagedProcess(
                pid,
                original,
                eligible,
                create_time_s=create_time_s,
                owner_uid=owner_uid,
                parent_pid=parent_pid,
            )
        except ValueError as exc:
            raise SafetyViolation(str(exc)) from exc
        self._targets[pid] = target
        return target

    @property
    def controller_pid(self) -> int:
        return self._controller_pid

    @property
    def targets(self) -> tuple[ManagedProcess, ...]:
        return tuple(self._targets.values())

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
    if len(requested) != len(set(requested)):
        raise SafetyViolation("requested affinity contains duplicate CPU IDs")
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
    def get_affinity(self, target: ManagedProcess) -> tuple[int, ...]:
        """Return the observed guest affinity mask for a managed process."""

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

    def pace(self, target: ManagedProcess, duration_ms: int, max_duration_ms: int) -> float:
        """Pause and resume a target, returning observed elapsed monotonic seconds."""

        if duration_ms <= 0:
            raise ActuationError("pace duration must be positive")
        if max_duration_ms <= 0 or duration_ms > max_duration_ms:
            raise ActuationError(
                f"pace duration {duration_ms} ms exceeds configured maximum {max_duration_ms} ms"
            )
        started = time.monotonic()
        try:
            self.pause(target)
            time.sleep(duration_ms / 1000.0)
        finally:
            self.resume(target)
        return time.monotonic() - started


class LinuxActuator(Actuator):
    """Controls only direct Linux child processes registered with this instance."""

    def __init__(
        self,
        registry: ManagedPidRegistry | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = ManagedPidRegistry() if registry is None else registry
        self._sleep = sleep
        self._monotonic = monotonic
        self._paused: set[int] = set()
        self._previous_handlers: dict[int, signal.Handlers] = {}

    def capabilities(self) -> ActuatorCapabilities:
        reasons: list[str] = []
        if not sys.platform.startswith("linux"):
            reasons.append("Linux guest required")
        affinity = sys.platform.startswith("linux") and hasattr(psutil.Process(), "cpu_affinity")
        pause_resume = sys.platform.startswith("linux") and hasattr(signal, "SIGSTOP")
        return ActuatorCapabilities(affinity, pause_resume, affinity and pause_resume, tuple(reasons))

    def register_child(
        self,
        pid: int,
        eligible_guest_cpus: tuple[int, ...] | list[int] | None = None,
    ) -> ManagedProcess:
        """Verify and capture the identity and full original mask of a direct child."""

        if not sys.platform.startswith("linux"):
            raise ActuationError("LinuxActuator requires a Linux guest")
        if pid in {0, 1, os.getpid(), os.getppid()} or pid <= 1:
            raise SafetyViolation(f"PID {pid} is protected and cannot be managed")
        try:
            process = psutil.Process(pid)
            parent_pid = process.ppid()
            owner_uid = process.uids().real
            create_time_s = process.create_time()
            original = tuple(sorted(process.cpu_affinity()))
        except (psutil.Error, OSError) as exc:
            logger.error("failed to inspect candidate PID %s: %s", pid, exc)
            raise ActuationError(f"failed to inspect candidate PID {pid}: {exc}") from exc
        if parent_pid != self.registry.controller_pid:
            raise SafetyViolation(
                f"PID {pid} is not a direct child of controller {self.registry.controller_pid}"
            )
        if owner_uid != os.getuid():
            raise SafetyViolation(f"PID {pid} is not owned by controller UID {os.getuid()}")
        target = self.registry.register_child(
            pid,
            original,
            eligible_guest_cpus,
            create_time_s=create_time_s,
            owner_uid=owner_uid,
            parent_pid=parent_pid,
        )
        logger.info(
            "registered managed child pid=%s create_time=%s original_mask=%s eligible_guest_cpus=%s",
            pid,
            create_time_s,
            original,
            target.eligible_guest_cpus,
        )
        return target

    def _process(self, target: ManagedProcess) -> psutil.Process:
        registered = self.registry.require_managed(target.pid)
        if registered != target:
            raise SafetyViolation(f"PID {target.pid} registration does not match supplied identity")
        try:
            process = psutil.Process(target.pid)
            if target.create_time_s is None or process.create_time() != target.create_time_s:
                raise SafetyViolation(f"PID {target.pid} identity changed; refusing control")
            if target.owner_uid is None or process.uids().real != target.owner_uid:
                raise SafetyViolation(f"PID {target.pid} ownership changed; refusing control")
            if target.parent_pid is None or process.ppid() != target.parent_pid:
                raise SafetyViolation(f"PID {target.pid} parent changed; refusing control")
            return process
        except SafetyViolation:
            raise
        except (psutil.Error, OSError) as exc:
            logger.error("managed child identity check failed pid=%s: %s", target.pid, exc)
            raise ActuationError(f"managed child PID {target.pid} is unavailable: {exc}") from exc

    def get_affinity(self, target: ManagedProcess) -> tuple[int, ...]:
        try:
            return tuple(sorted(self._process(target).cpu_affinity()))
        except SafetyViolation:
            raise
        except (psutil.Error, OSError) as exc:
            logger.error("affinity read failed pid=%s: %s", target.pid, exc)
            raise ActuationError(f"affinity read failed for PID {target.pid}: {exc}") from exc

    def set_affinity(self, target: ManagedProcess, cpu_ids: tuple[int, ...]) -> tuple[int, ...]:
        requested = tuple(sorted(validate_requested_cpus(target, cpu_ids)))
        process = self._process(target)
        try:
            process.cpu_affinity(list(requested))
            observed = tuple(sorted(process.cpu_affinity()))
        except (psutil.Error, OSError, ValueError) as exc:
            logger.error(
                "affinity apply failed pid=%s requested_mask=%s: %s", target.pid, requested, exc
            )
            raise ActuationError(f"affinity apply failed for PID {target.pid}: {exc}") from exc
        logger.info(
            "affinity pid=%s original_mask=%s requested_mask=%s observed_mask=%s",
            target.pid,
            target.original_affinity,
            requested,
            observed,
        )
        if observed != requested:
            try:
                process.cpu_affinity(list(target.original_affinity))
            except (psutil.Error, OSError, ValueError) as exc:
                logger.error("affinity rollback failed pid=%s: %s", target.pid, exc)
            raise ActuationError(
                f"affinity readback mismatch for PID {target.pid}: requested {requested}, observed {observed}"
            )
        return observed

    def _wait_for_pause_state(self, process: psutil.Process, *, stopped: bool) -> None:
        deadline = self._monotonic() + 0.5
        while self._monotonic() < deadline:
            try:
                observed = process.status() == psutil.STATUS_STOPPED
            except psutil.NoSuchProcess as exc:
                raise ActuationError(f"managed child PID {process.pid} exited during signal") from exc
            if observed is stopped:
                return
            self._sleep(0.005)
        state = "stop" if stopped else "resume"
        raise ActuationError(f"PID {process.pid} did not {state} within 0.5 seconds")

    def pause(self, target: ManagedProcess) -> None:
        process = self._process(target)
        try:
            process.send_signal(signal.SIGSTOP)
            self._paused.add(target.pid)
            self._wait_for_pause_state(process, stopped=True)
            logger.info("paused managed child pid=%s", target.pid)
        except ActuationError:
            raise
        except (psutil.Error, OSError) as exc:
            logger.error("pause failed pid=%s: %s", target.pid, exc)
            raise ActuationError(f"pause failed for PID {target.pid}: {exc}") from exc

    def resume(self, target: ManagedProcess) -> None:
        process = self._process(target)
        try:
            process.send_signal(signal.SIGCONT)
            self._wait_for_pause_state(process, stopped=False)
            self._paused.discard(target.pid)
            logger.info("resumed managed child pid=%s", target.pid)
        except ActuationError:
            raise
        except (psutil.Error, OSError) as exc:
            logger.error("resume failed pid=%s: %s", target.pid, exc)
            raise ActuationError(f"resume failed for PID {target.pid}: {exc}") from exc

    def pace(self, target: ManagedProcess, duration_ms: int, max_duration_ms: int) -> float:
        if duration_ms <= 0:
            raise ActuationError("pace duration must be positive")
        if max_duration_ms <= 0 or duration_ms > max_duration_ms:
            raise ActuationError(
                f"pace duration {duration_ms} ms exceeds configured maximum {max_duration_ms} ms"
            )
        started = self._monotonic()
        try:
            self.pause(target)
            self._sleep(duration_ms / 1000.0)
        finally:
            if target.pid in self._paused:
                self.resume(target)
        elapsed = self._monotonic() - started
        logger.info(
            "paced managed child pid=%s requested_ms=%s elapsed_s=%.6f",
            target.pid,
            duration_ms,
            elapsed,
        )
        return elapsed

    def restore(self, target: ManagedProcess) -> None:
        process = self._process(target)
        try:
            if target.pid in self._paused or process.status() == psutil.STATUS_STOPPED:
                process.send_signal(signal.SIGCONT)
                self._paused.discard(target.pid)
                self._wait_for_pause_state(process, stopped=False)
            process.cpu_affinity(list(target.original_affinity))
            observed = tuple(sorted(process.cpu_affinity()))
            if observed != tuple(sorted(target.original_affinity)):
                raise ActuationError(
                    f"restoration readback mismatch for PID {target.pid}: observed {observed}"
                )
            logger.info(
                "restored managed child pid=%s original_mask=%s observed_mask=%s",
                target.pid,
                target.original_affinity,
                observed,
            )
        except ActuationError:
            raise
        except (psutil.Error, OSError) as exc:
            logger.error("restore failed pid=%s: %s", target.pid, exc)
            raise ActuationError(f"restore failed for PID {target.pid}: {exc}") from exc

    def restore_all(self) -> tuple[str, ...]:
        errors: list[str] = []
        for target in reversed(self.registry.targets):
            try:
                self.restore(target)
            except (ActuationError, SafetyViolation) as exc:
                message = f"PID {target.pid}: {exc}"
                errors.append(message)
                logger.error("managed child cleanup failed: %s", message)
        return tuple(errors)

    def install_signal_handlers(self) -> None:
        if self._previous_handlers:
            return
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        logger.info("received signal=%s; restoring managed children", signum)
        self.restore_all()
        self.restore_signal_handlers()
        raise SystemExit(128 + signum)

    def __enter__(self) -> LinuxActuator:
        self.install_signal_handlers()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.restore_all()
        self.restore_signal_handlers()
