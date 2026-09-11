"""Small first-order thermal backend. Time advances only on reads and writes."""

import math
from collections.abc import Callable
from time import monotonic

from .controller import Status, _finite


class Simulator:
    """Use through Chiller, which owns access and setpoint validation.

    This uncalibrated model starts at 20 Celsius. It has no worker, GUI dependency
    or physical coolant/flow model. An injected clock makes tests repeatable.
    """

    def __init__(
        self, *, time_constant_s: float = 30.0, clock: Callable[[], float] = monotonic
    ) -> None:
        self._tau = _finite(time_constant_s, "time_constant_s")
        if self._tau <= 0:
            raise ValueError("time_constant_s must be positive")
        self._clock = clock
        self._connected = False
        self._updated = 0.0
        self._temperature = self._setpoint = 20.0

    def connect(self) -> None:
        if not self._connected:
            self._updated = self._clock()
            self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._advance()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _advance(self) -> None:
        if not self._connected:
            raise ConnectionError("Simulator is disconnected")
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._temperature = self._setpoint + (self._temperature - self._setpoint) * math.exp(
            -elapsed / self._tau
        )
        self._updated = now

    def _read_temperature(self) -> float:
        self._advance()
        return self._temperature

    def _read_setpoint(self) -> float:
        if not self._connected:
            raise ConnectionError("Simulator is disconnected")
        return self._setpoint

    def _write_setpoint(self, value: float) -> None:
        self._advance()
        self._setpoint = value

    def _read_status(self) -> Status:
        return Status(self._connected, "simulator", "Simulation; no physical safety telemetry")
