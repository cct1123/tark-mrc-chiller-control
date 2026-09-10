"""Deterministic first-order thermal simulation, not a calibrated hardware model."""

import math
import random
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from typing import Literal

from .device import DeviceStatus
from .errors import (
    ChillerConnectionError,
    ChillerTimeoutError,
    SetpointValidationError,
    TransportError,
)
from .safety import DISTILLED_WATER, CoolantProfile, _finite_real

SimulatedOperation = Literal[
    "connect", "get_temperature", "get_setpoint", "set_setpoint", "get_status"
]
SimulatedFault = Literal["timeout", "disconnect"]
_OPERATIONS = ("connect", "get_temperature", "get_setpoint", "set_setpoint", "get_status")


class SimulatedDevice:
    """A software backend with explicit connection and no serial dependency.

    Thermal time advances only while connected. Disconnect preserves the target;
    reconnect resumes the model without issuing or replaying a setpoint write.
    Measurement cadence holds the last sampled value until the interval elapses;
    readings are produced on demand, with no simulation worker. Zero interval
    preserves continuous readings. Seeded noise affects measurement only, not
    the thermal state. These are test conveniences, not MRC performance claims.
    """

    def __init__(
        self,
        *,
        coolant: CoolantProfile = DISTILLED_WATER,
        clock: Callable[[], float] = time.monotonic,
        initial_temperature_c: float = 20.0,
        time_constant_s: float = 30.0,
        measurement_interval_s: float = 0.0,
        noise_std_c: float = 0.0,
        seed: int | None = None,
    ) -> None:
        if not isinstance(coolant, CoolantProfile):
            raise SetpointValidationError("coolant must be an explicit CoolantProfile")
        self._coolant = coolant
        self._temperature_c = coolant.validate(initial_temperature_c)
        self._setpoint_c = self._temperature_c
        self._time_constant_s = _finite_real(time_constant_s, "Simulation time constant (s)")
        if self._time_constant_s <= 0:
            raise SetpointValidationError("Simulation time constant must be positive (s)")
        self._measurement_interval_s = _finite_real(
            measurement_interval_s, "Measurement interval (s)"
        )
        self._noise_std_c = _finite_real(noise_std_c, "Measurement noise standard deviation (°C)")
        if self._measurement_interval_s < 0 or self._noise_std_c < 0:
            raise SetpointValidationError("Measurement interval and noise must be nonnegative")
        if seed is not None and type(seed) is not int:
            raise SetpointValidationError("Simulation seed must be an integer or None")
        self._random = random.Random(seed)
        self._measurement_c = self._temperature_c
        self._last_measurement: float | None = None
        self._clock = clock
        self._last_update: float | None = None
        self._connected = False
        self._lock = threading.RLock()
        self._faults: dict[str, deque[tuple[SimulatedFault, int]]] = {}
        self._operation_counts: Counter[str] = Counter()

    @property
    def coolant(self) -> CoolantProfile:
        return self._coolant

    @property
    def operation_counts(self) -> dict[str, int]:
        """Copy of attempted operations, excluding rejected invalid writes."""
        with self._lock:
            return dict(self._operation_counts)

    def inject_fault(
        self, operation: SimulatedOperation, fault: SimulatedFault, *, count: int = 1
    ) -> None:
        """Fail the next count matching operations before changing a target.

        Both failures invalidate connection, pause thermal time and preserve the
        current target. Invalid setpoints never consume a queued write fault.
        Explicit reconnect is possible after the finite fault script is consumed.
        """
        if operation not in _OPERATIONS or fault not in ("timeout", "disconnect"):
            raise ValueError("Unknown simulated operation or fault")
        if type(count) is not int or count <= 0:
            raise ValueError("Fault count must be a positive integer")
        with self._lock:
            self._faults.setdefault(operation, deque()).append((fault, count))

    def _attempt(self, operation: SimulatedOperation) -> None:
        self._operation_counts[operation] += 1
        pending = self._faults.get(operation)
        if not pending:
            return
        fault, count = pending.popleft()
        if count > 1:
            pending.appendleft((fault, count - 1))
        self.disconnect()
        if fault == "timeout":
            raise ChillerTimeoutError(f"Simulated {operation} timed out")
        raise TransportError(f"Simulated connection lost during {operation}")

    def connect(self) -> None:
        with self._lock:
            if not self._connected:
                self._attempt("connect")
                self._last_update = self._clock()
                self._last_measurement = None
                self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            if self._connected:
                self._advance()
                self._connected = False
                self._last_update = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise ChillerConnectionError("Simulated chiller is disconnected")

    def _advance(self) -> None:
        now = self._clock()
        assert self._last_update is not None
        elapsed = max(0.0, now - self._last_update)
        self._temperature_c = self._setpoint_c + (
            self._temperature_c - self._setpoint_c
        ) * math.exp(-elapsed / self._time_constant_s)
        self._last_update = now

    def get_temperature(self) -> float:
        with self._lock:
            self._require_connected()
            self._attempt("get_temperature")
            self._advance()
            assert self._last_update is not None
            if (
                self._last_measurement is None
                or self._last_update - self._last_measurement >= self._measurement_interval_s
            ):
                variation = self._random.gauss(0, self._noise_std_c) if self._noise_std_c else 0.0
                self._measurement_c = self._temperature_c + variation
                self._last_measurement = self._last_update
            return self._measurement_c

    def get_setpoint(self) -> float:
        with self._lock:
            self._require_connected()
            self._attempt("get_setpoint")
            return self._setpoint_c

    def set_setpoint(self, value_c: float) -> None:
        validated = self._coolant.validate(value_c)
        with self._lock:
            self._require_connected()
            self._attempt("set_setpoint")
            self._advance()
            self._setpoint_c = validated

    def get_status(self) -> DeviceStatus:
        with self._lock:
            self._attempt("get_status")
            return DeviceStatus(
                connected=self._connected,
                backend="simulator",
                detail="Simulation only; physical coolant, flow, level and leaks are unverified.",
            )
