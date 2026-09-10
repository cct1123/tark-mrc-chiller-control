"""GUI-independent polling and bounded, immutable live snapshots."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from threading import TIMEOUT_MAX, Event, Lock, Thread
from time import monotonic
from typing import TYPE_CHECKING

from .api import Chiller
from .device import DeviceStatus
from .errors import ChillerError

if TYPE_CHECKING:
    from .csvlog import CsvLogger


@dataclass(frozen=True)
class Sample:
    timestamp_utc: datetime
    elapsed_s: float
    temperature_c: float | None
    setpoint_c: float | None
    status: DeviceStatus
    error: str | None = None


@dataclass(frozen=True)
class LiveSnapshot:
    history: tuple[Sample, ...]
    running: bool
    service_error: str | None
    logging_error: str | None

    @property
    def latest(self) -> Sample | None:
        return self.history[-1] if self.history else None


class LiveState:
    def __init__(self, capacity: int = 3600):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("history capacity must be a positive integer")
        self._history: deque[Sample] = deque(maxlen=capacity)
        self._lock = Lock()
        self._running = False
        self._service_error: str | None = None
        self._logging_error: str | None = None

    def append(self, sample: Sample) -> None:
        with self._lock:
            self._history.append(sample)

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
                tuple(self._history), self._running, self._service_error, self._logging_error
            )


def positive_seconds(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be positive finite seconds")
    # Check integer bounds before conversion: huge ints can overflow isfinite.
    if value <= 0 or value > TIMEOUT_MAX or not isfinite(value):
        raise ValueError(f"{name} must be positive finite seconds no greater than {TIMEOUT_MAX:g}")
    return float(value)


class Monitor:
    """One worker; caller owns connection and logger lifetimes.

    Poll errors remain visible without replaying writes or reconnecting. Unexpected
    errors stop the worker and are retained in live state. Logging failure disables
    further CSV writes for this run while temperature monitoring continues.
    """

    def __init__(
        self,
        chiller: Chiller,
        state: LiveState,
        *,
        interval_s: float = 1.0,
        logger: "CsvLogger | None" = None,
    ):
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

    def poll_once(self) -> Sample:
        with self._poll_lock:
            # Use the start of the sequential poll: a slow later read must not
            # make an earlier temperature look freshly acquired.
            timestamp = datetime.now(UTC)
            elapsed = monotonic() - self._origin
            try:
                status = self.chiller.get_status()
                self._backend = status.backend
                temperature = self.chiller.get_temperature()
                setpoint = self.chiller.get_setpoint()
                error = None
            except ChillerError as exc:
                # Connection describes the last successful poll, not port ownership.
                status = DeviceStatus(False, self._backend, "Poll failed; telemetry unavailable")
                temperature = setpoint = None
                error = f"{type(exc).__name__}: {exc}"
            sample = Sample(
                timestamp,
                elapsed,
                temperature,
                setpoint,
                status,
                error,
            )
            if self._logger is not None:
                try:
                    self._logger.write(sample)
                except (OSError, ValueError) as exc:
                    self.state.set_error(f"CSV recording stopped: {exc}", logging=True)
                    self._logger = None
            self.state.append(sample)
            return sample

    def start(self) -> None:
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            self.state.clear_service_error()
            self._stop.clear()
            self._thread = Thread(target=self._run, name="chiller-monitor", daemon=False)
            self.state.set_running(True)
            try:
                self._thread.start()
            except Exception as exc:
                self._thread = None
                self.state.set_running(False)
                self.state.set_error(f"Monitoring could not start: {exc}")
                raise

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = monotonic()
                self.poll_once()
                self._stop.wait(max(0.0, self.interval_s - (monotonic() - started)))
        except Exception as exc:
            self.state.set_error(f"Monitoring stopped: {type(exc).__name__}: {exc}")
        finally:
            self.state.set_running(False)

    def stop(self, timeout_s: float = 5.0) -> None:
        timeout_s = positive_seconds(timeout_s, "stop timeout")
        with self._lifecycle:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout_s)
                if self._thread.is_alive():
                    self.state.set_error("Monitor stop timed out; worker is still active")
                    raise TimeoutError("Monitor still active; do not close its device/logger yet")
                self._thread = None
            self.state.clear_service_error("Monitor stop timed out; worker is still active")
            self.state.set_running(False)
