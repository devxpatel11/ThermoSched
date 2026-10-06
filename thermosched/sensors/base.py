"""Hardware-independent thermal sensor contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from thermosched.models import ThermalKind, ThermalSnapshot


class SensorProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend name shown in logs and doctor output."""

    @property
    @abstractmethod
    def thermal_kind(self) -> ThermalKind:
        """Return the provenance produced by this backend."""

    @abstractmethod
    def sample(self, sampled_at_s: float) -> ThermalSnapshot:
        """Read one snapshot without making scheduling decisions."""
