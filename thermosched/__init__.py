"""ThermoSched public package contracts."""

from thermosched.config import SchedulerConfig, load_config
from thermosched.controller import Controller, ControlEvent, PidSchedulerState, RunSummary
from thermosched.models import (
    ActuatorCapabilities,
    CoreSample,
    CpuIdMap,
    Decision,
    DecisionAction,
    EnvironmentMetadata,
    ManagedProcess,
    ProcessSample,
    ReplayFrame,
    ThermalKind,
    ThermalSnapshot,
)
from thermosched.scheduler.actuator import ActuationError, LinuxActuator, SafetyViolation

__all__ = [
    "ActuatorCapabilities",
    "ActuationError",
    "Controller",
    "ControlEvent",
    "CoreSample",
    "CpuIdMap",
    "Decision",
    "DecisionAction",
    "EnvironmentMetadata",
    "ManagedProcess",
    "LinuxActuator",
    "PidSchedulerState",
    "ProcessSample",
    "ReplayFrame",
    "SchedulerConfig",
    "RunSummary",
    "SafetyViolation",
    "ThermalKind",
    "ThermalSnapshot",
    "load_config",
]

__version__ = "0.1.0"
