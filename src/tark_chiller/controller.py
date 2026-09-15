"""Synchronous device access. Construction does not open or start anything."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from threading import TIMEOUT_MAX, Event, Lock, RLock
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitor import Monitor
    from .serial import SerialDevice


class ProtocolError(RuntimeError):
    """Missing or invalid protocol; automatic read recovery is suspended."""


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _seconds(value: object, name: str, *, allow_zero: bool = False) -> float:
    seconds = _finite(value, name)
    minimum_ok = seconds >= 0 if allow_zero else seconds > 0
    if not minimum_ok or seconds > TIMEOUT_MAX:
        limit = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {limit} and at most {TIMEOUT_MAX:g} seconds")
    return seconds


def _reading(value: object) -> float:
    try:
        return _finite(value, "Celsius reading")
    except ValueError as exc:
        raise ProtocolError(f"Invalid device reading: {value!r}") from exc


@dataclass(frozen=True)
class Status:
    connected: bool
    backend: str
    detail: str = ""


class Chiller:
    """Own one backend and optional monitor. Never share a backend between drivers.

    Temperatures are Celsius. Custom limits need a coolant name and source.
    Read recovery is bounded per outage; writes are never retried or replayed.
    """

    def __init__(
        self,
        backend: "SerialDevice | Simulator",
        *,
        setpoint_range: tuple[float, float] = (2.0, 40.0),
        coolant: str = "distilled water",
        coolant_source: str | None = None,
        reconnect_attempts: int = 0,
        reconnect_delay_s: float = 0.05,
    ) -> None:
        if not isinstance(setpoint_range, tuple) or len(setpoint_range) != 2:
            raise ValueError("setpoint_range must be a (minimum, maximum) Celsius tuple")
        low, high = (_finite(v, "setpoint limit") for v in setpoint_range)
        if low >= high or not isinstance(coolant, str) or not coolant.strip():
            raise ValueError("Provide ordered setpoint limits and a coolant name")
        if coolant_source is not None and (
            not isinstance(coolant_source, str) or not coolant_source.strip()
        ):
            raise ValueError("coolant_source must be nonempty text")
        if ((low, high) != (2, 40) or coolant != "distilled water") and coolant_source is None:
            raise ValueError("Custom coolant limits require a documented coolant_source")
        if type(reconnect_attempts) is not int or not 0 <= reconnect_attempts <= 10:
            raise ValueError("reconnect_attempts must be an integer from 0 to 10")
        delay = _finite(reconnect_delay_s, "reconnect_delay_s")
        if not 0 < delay <= 60:
            raise ValueError("reconnect_delay_s must be greater than 0 and at most 60")
        self._backend = backend
        self._setpoint_range = (low, high)
        self._coolant = coolant
        self._coolant_source = coolant_source or "MRC150/300 User Manual Rev 13, p7"
        self._reconnect_attempts = reconnect_attempts
        self._reconnects_remaining = reconnect_attempts
        self._reconnect_delay_s = delay
        self._lock = RLock()
        self._intent_lock = Lock()
        self._cancel = Event()
        self._cancel.set()
        self._fault = ""
        self._backend_name = "unknown"
        self._monitor_lock = Lock()
        self._monitor: Monitor | None = None

    @property
    def setpoint_range(self) -> tuple[float, float]:
        return self._setpoint_range

    @property
    def coolant(self) -> str:
        return self._coolant

    @property
    def coolant_source(self) -> str:
        return self._coolant_source

    def _intent(self, connected: bool) -> Event:
        """Cancel older requests before waiting for device I/O."""
        with self._intent_lock:
            self._cancel.set()
            self._cancel = Event()
            if not connected:
                self._cancel.set()
            return self._cancel

    @staticmethod
    def _check_cancel(cancel: Event) -> None:
        if cancel.is_set():
            raise ConnectionError("Connection cancelled or disconnected; call connect() explicitly")

    def _require_connected(self) -> None:
        self._check_cancel(self._cancel)
        if self._fault:
            raise ConnectionError(self._fault)
        if not self._backend.is_connected:
            raise ConnectionError("Chiller is disconnected")

    def connect(self) -> None:
        token = self._intent(connected=True)
        with self._lock:
            self._check_cancel(token)
            self._reconnects_remaining = self._reconnect_attempts
            self._fault = ""
            self._read(self._open, token, connecting=True)

    def _open(self) -> None:
        self._backend.connect()
        if not self._backend.is_connected:
            raise ConnectionError("Backend connect returned without an active connection")

    def disconnect(self) -> None:
        """Cancel recovery, close the connection, join polling and close its CSV."""
        token = self._intent(connected=False)
        with self._monitor_lock:
            if self._monitor is not None:
                self._monitor._stop.set()
            try:
                with self._lock:
                    if token is self._cancel:
                        self._backend.disconnect()
            finally:
                if self._monitor is not None:
                    self._monitor._join(5.0)

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return not self._cancel.is_set() and not self._fault and self._backend.is_connected

    def _suspend(self, message: str, error: BaseException) -> None:
        self._fault = message
        try:
            self._backend.disconnect()
        except OSError as cleanup:
            error.add_note(str(cleanup))

    def _read[T](self, operation: Callable[[], T], token: Event, *, connecting: bool = False) -> T:
        """Recover reads and connection attempts; writes never enter this loop."""
        retry = False
        while True:
            try:
                self._check_cancel(token)
                if self._fault:
                    raise ConnectionError(self._fault)
                if retry:
                    self._backend.disconnect()
                    token.wait(self._reconnect_delay_s)
                    self._check_cancel(token)
                    if not connecting:
                        self._open()
                        self._check_cancel(token)
                elif not connecting:
                    self._require_connected()
                result = operation()
                self._check_cancel(token)
                if not connecting:
                    self._reconnects_remaining = self._reconnect_attempts
                return result
            except ProtocolError as exc:
                self._suspend(f"Protocol failure; recovery suspended: {exc}", exc)
                raise
            except OSError as exc:
                self._check_cancel(token)
                if self._fault or self._reconnect_attempts == 0:
                    raise
                if self._reconnects_remaining:
                    self._reconnects_remaining -= 1
                    retry = True
                    continue
                self._suspend(f"Recovery exhausted: {exc}; call connect() explicitly", exc)
                raise ConnectionError(self._fault) from exc

    def read_temperature(self) -> float:
        with self._lock:
            return self._read(lambda: _reading(self._backend._read_temperature()), self._cancel)

    def read_setpoint(self) -> float:
        with self._lock:
            return self._read(lambda: _reading(self._backend._read_setpoint()), self._cancel)

    def set_setpoint(self, value_c: object) -> None:
        value = _finite(value_c, "Setpoint (Celsius)")
        low, high = self._setpoint_range
        if not low <= value <= high:
            raise ValueError(
                f"Setpoint {value:g} Celsius is outside {self.coolant} [{low:g}, {high:g}]"
            )
        with self._intent_lock:
            token = self._cancel
        with self._lock:
            self._check_cancel(token)
            self._require_connected()
            try:
                self._backend._write_setpoint(value)
            except ProtocolError as exc:
                self._suspend(f"Protocol failure; recovery suspended: {exc}", exc)
                raise

    def read_status(self) -> Status:
        with self._lock:
            if self._cancel.is_set() or self._fault:
                return Status(
                    connected=False,
                    backend=self._backend_name,
                    detail=self._fault or "disconnected",
                )

            def read() -> Status:
                status = self._backend._read_status()
                if not isinstance(status, Status) or (
                    type(status.connected) is not bool
                    or not isinstance(status.backend, str)
                    or not status.backend.strip()
                    or not isinstance(status.detail, str)
                ):
                    raise ProtocolError("Backend returned an invalid status")
                self._backend_name = status.backend
                return status

            return self._read(read, self._cancel)

    def start_monitoring(
        self,
        *,
        interval_s: float = 1.0,
        csv_path: str | Path | None = None,
        history_size: int = 3600,
    ) -> "Monitor":
        """Start sampling explicitly; snapshot() on the returned handle reads memory only."""
        from .monitor import Monitor

        with self._monitor_lock:
            with self._lock:
                self._require_connected()
            if self._monitor is not None and self._monitor.running:
                raise RuntimeError("Monitoring already started; stop it before another run")
            monitor = Monitor(
                self, interval_s=interval_s, csv_path=csv_path, history_size=history_size
            )
            self._monitor = monitor
            monitor._start()
            return monitor

    def stop_monitoring(self, timeout_s: float = 5.0) -> None:
        """Join polling and close its CSV; keep the connection for synchronous use."""
        with self._monitor_lock:
            if self._monitor is not None:
                self._monitor._join(timeout_s)

    def __enter__(self) -> "Chiller":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.disconnect()


class Simulator:
    """Use through Chiller, which owns access and setpoint validation.

    This uncalibrated model starts at 20 Celsius. It has no worker, GUI dependency
    or physical coolant/flow model. An injected clock makes tests repeatable.
    """

    def __init__(
        self,
        *,
        time_constant_s: float = 30.0,
        clock: Callable[[], float] = monotonic,
        initial_setpoint_c: float = 20.0,
    ) -> None:
        self._tau = _finite(time_constant_s, "time_constant_s")
        if self._tau <= 0:
            raise ValueError("time_constant_s must be positive")
        self._clock = clock
        self._connected = False
        self._updated = 0.0
        self._temperature = 20.0
        self._setpoint = _finite(initial_setpoint_c, "initial_setpoint_c")

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
