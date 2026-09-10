"""Explicit coolant policy; this cannot establish the liquid actually installed."""

import math
from dataclasses import dataclass
from numbers import Real

from .errors import SetpointValidationError


def _finite_real(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SetpointValidationError(f"{label} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise SetpointValidationError(f"{label} must be a finite real number") from exc
    if not math.isfinite(result):
        raise SetpointValidationError(f"{label} must be a finite real number")
    return result


@dataclass(frozen=True)
class CoolantProfile:
    """Documented policy bounds, independent of controller-accepted values.

    A caller selecting another coolant remains responsible for establishing that
    its source applies to the actual liquid, unit and operating conditions.
    """

    name: str
    minimum_c: float
    maximum_c: float
    source: str

    def __post_init__(self) -> None:
        for field_name in ("name", "source"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise SetpointValidationError(f"Coolant {field_name} must be nonempty text")
        minimum = _finite_real(self.minimum_c, "Coolant minimum (°C)")
        maximum = _finite_real(self.maximum_c, "Coolant maximum (°C)")
        if minimum >= maximum:
            raise SetpointValidationError("Coolant minimum must be below maximum (°C)")
        object.__setattr__(self, "minimum_c", minimum)
        object.__setattr__(self, "maximum_c", maximum)

    def validate(self, value_c: object) -> float:
        """Return a normalized setpoint, or fail before any device operation."""
        result = _finite_real(value_c, "Setpoint (°C)")
        if not self.minimum_c <= result <= self.maximum_c:
            raise SetpointValidationError(
                f"Setpoint {result:g} °C is outside {self.name} policy "
                f"[{self.minimum_c:g}, {self.maximum_c:g}] °C"
            )
        return result


DISTILLED_WATER = CoolantProfile(
    name="distilled water",
    minimum_c=2.0,
    maximum_c=40.0,
    source="MRC150/300 Specification and User Manual, Rev 13 (2024-03-04), p7; records/RECORDS.md E002",
)
