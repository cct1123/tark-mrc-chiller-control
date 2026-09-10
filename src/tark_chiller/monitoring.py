"""GUI-independent polling and bounded, immutable live snapshots."""

import csv
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from time import monotonic, perf_counter
from typing import Protocol
from weakref import ReferenceType, WeakKeyDictionary, ref

from .api import Chiller
from .device import DeviceStatus
from .errors import ChillerConnectionError, ChillerError

_OWNERS: WeakKeyDictionary[object, ReferenceType["Monitor"]] = WeakKeyDictionary()
_OWNERS_LOCK = Lock()
_STOP_TIMEOUT = "Monitor stop timed out; worker or poll is still active"


@dataclass(frozen=True)
class Sample:
    timestamp_utc: datetime
    elapsed_s: float
    temperature_c: float | None
    setpoint_c: float | None
    status: DeviceStatus
    error: str | None = None
    sampled_monotonic: float | None = None


class SampleSink(Protocol):
    def write(self, sample: Sample) -> None: ...


@dataclass(frozen=True)
class LiveSnapshot:
    history: tuple[Sample, ...]
    running: bool
    service_error: str | None
    logging_error: str | None
    logging_enabled: bool = False
    logged_samples: int = 0
    sample_count: int = 0
    failed_samples: int = 0

    @property
    def latest(self) -> Sample | None:
        return self.history[-1] if self.history else None


class LiveState:
    def __init__(self, capacity: int = 3600) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("history capacity must be a positive integer")
        self._history: deque[Sample] = deque(maxlen=capacity)
        self._lock = Lock()
        self._running = False
        self._service_error: str | None = None
        self._logging_error: str | None = None
        self._logging_enabled = False
        self._logged_samples = 0
        self._sample_count = 0
        self._failed_samples = 0

    def append(self, sample: Sample, *, logged: bool = False) -> None:
        with self._lock:
            self._history.append(sample)
            self._sample_count += 1
            self._failed_samples += int(sample.error is not None)
            self._logged_samples += int(logged)
            if logged:
                self._logging_error = None

    def set_logging(self, enabled: bool) -> None:
        with self._lock:
            self._logging_enabled = enabled

    def set_running(self, running: bool) -> None:
        with self._lock:
            self._running = running

    def set_error(self, message: str, *, logging: bool = False) -> None:
        with self._lock:
            if logging:
                self._logging_error = message
            else:
                self._service_error = message

    def clear_service_error(self, expected: str | None = None) -> None:
        with self._lock:
            if expected is None or self._service_error == expected:
                self._service_error = None

    def snapshot(self) -> LiveSnapshot:
        with self._lock:
            return LiveSnapshot(
                tuple(self._history),
                self._running,
                self._service_error,
                self._logging_error,
                self._logging_enabled,
                self._logged_samples,
                self._sample_count,
                self._failed_samples,
            )


def positive_seconds(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be positive finite seconds")
    # Check integer bounds before conversion: huge ints can overflow isfinite.
    if value <= 0 or value > TIMEOUT_MAX or not isfinite(value):
        raise ValueError(f"{name} must be positive finite seconds no greater than {TIMEOUT_MAX:g}")
    return float(value)


class Monitor:
    """One acquisition owner per Chiller and LiveState; caller owns their lifetimes.

    Read recovery follows the Chiller's finite policy; writes are never replayed. Unexpected
    errors stop the worker and are retained in live state. Logging failure disables
    further CSV writes for this run while temperature monitoring continues.
    """

    def __init__(
        self,
        chiller: Chiller,
        state: LiveState,
        *,
        interval_s: float = 1.0,
        logger: SampleSink | None = None,
    ) -> None:
        self.chiller = chiller
        self.state = state
        self.interval_s = positive_seconds(interval_s, "interval")
        self._logger = logger
        self._stop = Event()
        self._lifecycle = Lock()
        self._poll_lock = Lock()
        self._thread: Thread | None = None
        self._origin = monotonic()
        self._backend = "unknown"

    def _claim(self) -> None:
        with _OWNERS_LOCK:
            for resource in (self.chiller, self.state):
                owner = _OWNERS.get(resource)
                if owner is not None and owner() not in (None, self):
                    raise RuntimeError("Chiller or live state already has an acquisition owner")
            for resource in (self.chiller, self.state):
                _OWNERS[resource] = ref(self)

    def _release(self) -> None:
        with _OWNERS_LOCK:
            for resource in (self.chiller, self.state):
                owner = _OWNERS.get(resource)
                if owner is not None and owner() is self:
                    del _OWNERS[resource]

    def poll_once(self) -> Sample:
        """Perform one synchronous poll only when no worker or other poll is active.

        A new explicit poll after stop is permitted. Calls competing with a
        lifecycle transition are rejected instead of being queued past shutdown.
        """
        if not self._lifecycle.acquire(blocking=False):
            raise RuntimeError("Monitor lifecycle is changing; retry the poll after it completes")
        try:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Manual polling is unavailable while the monitor worker runs")
            if not self._poll_lock.acquire(blocking=False):
                raise RuntimeError("A monitor poll is already active")
            try:
                self._claim()
                self.state.set_logging(self._logger is not None)
            except BaseException:
                self._poll_lock.release()
                raise
        finally:
            self._lifecycle.release()
        try:
            return self._poll()
        finally:
            self.state.clear_service_error(_STOP_TIMEOUT)
            self._release()
            self._poll_lock.release()

    def _poll(self) -> Sample:
        # Use the start of the sequential poll: a slow later read must not
        # make an earlier temperature look freshly acquired.
        timestamp = datetime.now(UTC)
        sampled_monotonic = monotonic()
        elapsed = sampled_monotonic - self._origin
        temperature: float | None
        setpoint: float | None
        status: DeviceStatus | None = None
        try:
            status = self.chiller.get_status()
            self._backend = status.backend
            temperature = self.chiller.get_temperature()
            setpoint = self.chiller.get_setpoint()
            if not status.connected or not self.chiller.is_connected:
                raise ChillerConnectionError(status.detail or "Disconnected during poll")
            error = None
        except ChillerError as exc:
            # Connection describes the last successful poll, not port ownership.
            # Retain a completed status response's diagnostic without an
            # extra device transaction or reconnect merely to format an error.
            detail = (
                status.detail
                if status is not None and not status.connected and status.detail
                else str(exc)
            )
            status = DeviceStatus(False, self._backend, detail)
            temperature = setpoint = None
            error = f"{type(exc).__name__}: {detail}"
        sample = Sample(
            timestamp,
            elapsed,
            temperature,
            setpoint,
            status,
            error,
            sampled_monotonic,
        )
        logged = False
        if self._logger is not None:
            try:
                self._logger.write(sample)
                logged = True
            except (OSError, ValueError, csv.Error) as exc:
                self.state.set_error(f"CSV recording stopped: {exc}", logging=True)
                self._logger = None
                self.state.set_logging(False)
        self.state.append(sample, logged=logged)
        return sample

    def start(self) -> None:
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._poll_lock.locked():
                raise RuntimeError("Cannot start the worker while a manual poll is active")
            self._claim()
            self.state.clear_service_error()
            self.state.set_logging(self._logger is not None)
            self._stop.clear()
            self._thread = Thread(target=self._run, name="chiller-monitor", daemon=False)
            self.state.set_running(True)
            try:
                self._thread.start()
            except BaseException as exc:
                self._thread = None
                self.state.set_running(False)
                self.state.set_error(f"Monitoring could not start: {exc}")
                self._release()
                raise

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = monotonic()
                with self._poll_lock:
                    self._poll()
                self._stop.wait(max(0.0, self.interval_s - (monotonic() - started)))
        except Exception as exc:
            self.state.set_error(f"Monitoring stopped: {type(exc).__name__}: {exc}")
        finally:
            self.state.clear_service_error(_STOP_TIMEOUT)
            self.state.set_running(False)
            self._release()

    def request_stop(self) -> None:
        """Stop scheduling polls; caller must still call stop() to join the worker.

        This lets an application request stop, disconnect the serialized Chiller
        to cancel recovery, then join before closing its logger.
        """
        self._stop.set()

    def stop(self, timeout_s: float = 5.0) -> None:
        timeout_s = positive_seconds(timeout_s, "stop timeout")
        if self._thread is current_thread():
            raise RuntimeError("The monitor worker cannot join itself; use request_stop()")
        with self._lifecycle:
            self.request_stop()
            deadline = perf_counter() + timeout_s
            if self._thread is not None:
                self._thread.join(max(0.0, deadline - perf_counter()))
                if self._thread.is_alive():
                    self.state.set_error(_STOP_TIMEOUT)
                    raise TimeoutError("Monitor still active; do not close its device/logger yet")
                self._thread = None
            if not self._poll_lock.acquire(timeout=max(0.0, deadline - perf_counter())):
                self.state.set_error(_STOP_TIMEOUT)
                raise TimeoutError("Monitor poll still active; do not close its device/logger yet")
            self._poll_lock.release()
