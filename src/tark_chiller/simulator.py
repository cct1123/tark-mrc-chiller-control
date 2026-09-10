"""Deterministic first-order thermal simulation, not a calibrated hardware model."""

import math
import threading
import time
from collections.abc import Callable

from .device import DeviceStatus
from .errors import ChillerConnectionError, SetpointValidationError
from .safety import DISTILLED_WATER, CoolantProfile, _finite_real


class SimulatedDevice:
    """A software backend with explicit connection and no serial dependency.

    Thermal time advances only while connected. Disconnect preserves the target;
    reconnect resumes the model without issuing or replaying a setpoint write.
    """

    def __init__(
        self,
        *,
        coolant: CoolantProfile = DISTILLED_WATER,
        clock: Callable[[], float] = time.monotonic,
        initial_temperature_c: float = 20.0,
        time_constant_s: float = 30.0,
    ):
        if not isinstance(coolant, CoolantProfile):
            raise SetpointValidationError("coolant must be an explicit CoolantProfile")
        self._coolant = coolant
        self._temperature_c = coolant.validate(initial_temperature_c)
        self._setpoint_c = self._temperature_c
        self._time_constant_s = _finite_real(time_constant_s, "Simulation time constant (s)")
        if self._time_constant_s <= 0:
            raise SetpointValidationError("Simulation time constant must be positive (s)")
        self._clock = clock
        self._last_update: float | None = None
        self._connected = False
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            if not self._connected:
                self._last_update = self._clock()
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
            self._advance()
            return self._temperature_c

    def get_setpoint(self) -> float:
        with self._lock:
            self._require_connected()
            return self._setpoint_c

    def set_setpoint(self, value_c: float) -> None:
        validated = self._coolant.validate(value_c)
        with self._lock:
            self._require_connected()
            self._advance()
            self._setpoint_c = validated

    def get_status(self) -> DeviceStatus:
        with self._lock:
            return DeviceStatus(
                connected=self._connected,
                backend="simulator",
                detail="Simulation only; physical coolant, flow, level and leaks are unverified.",
            )
