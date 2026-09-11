"""Optional polling, bounded history and one CSV file per run."""

import csv
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from typing import TextIO

from .controller import Chiller, Status, _finite
from .errors import ProtocolError


def _seconds(value: object, name: str) -> float:
    seconds = _finite(value, name)
    if not 0 < seconds <= TIMEOUT_MAX:
        raise ValueError(f"{name} must be positive and at most {TIMEOUT_MAX:g} seconds")
    return seconds


@dataclass(frozen=True)
class Sample:
    timestamp_utc: datetime
    elapsed_s: float
    temperature_c: float | None
    setpoint_c: float | None
    status: Status
    error: str = ""
    sampled_monotonic: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    history: tuple[Sample, ...]
    running: bool
    service_error: str
    logging_error: str
    logging_enabled: bool
    logged_samples: int
    sample_count: int
    failed_samples: int

    @property
    def latest(self) -> Sample | None:
        return self.history[-1] if self.history else None


class Monitor:
    """Created by Chiller.start_monitoring(); read snapshot() from any client."""

    def __init__(
        self,
        chiller: Chiller,
        *,
        interval_s: float,
        csv_path: str | Path | None,
        history_size: int,
    ) -> None:
        self.interval_s = _seconds(interval_s, "Sampling interval")
        if type(history_size) is not int or history_size <= 0:
            raise ValueError("history_size must be a positive integer")
        self._chiller = chiller
        self._history: deque[Sample] = deque(maxlen=history_size)
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._running = False
        self._service_error = self._logging_error = ""
        self._logged = self._count = self._failed = 0
        self._file: TextIO | None = None
        self._writer = None
        if csv_path is not None:
            path = Path(csv_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("x", newline="", encoding="utf-8")
            try:
                self._writer = csv.writer(self._file)
                self._writer.writerow(
                    [
                        "timestamp_utc",
                        "elapsed_s",
                        "temperature_c",
                        "setpoint_c",
                        "backend",
                        "connected",
                        "status",
                        "error",
                    ]
                )
                self._file.flush()
            except BaseException:
                self._file.close()
                self._file = None
                raise

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                tuple(self._history),
                self._running,
                self._service_error,
                self._logging_error,
                self._file is not None,
                self._logged,
                self._count,
                self._failed,
            )

    def _close_csv(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError as exc:
                with self._lock:
                    self._logging_error = f"CSV close failed: {exc}"
            finally:
                with self._lock:
                    self._file = None
                    self._writer = None

    def _start(self) -> None:
        ready: Event | None = None
        try:
            ready = Event()
            self._thread = Thread(
                target=self._run, args=(ready,), name="chiller-monitor", daemon=False
            )
            self._running = True
            self._thread.start()
        except BaseException:
            self._stop.set()
            if self._thread is None or self._thread.ident is None:
                self._running = False
                self._close_csv()
            raise
        finally:
            if ready is not None:
                ready.set()

    def _request_stop(self) -> None:
        self._stop.set()

    def _join(self, timeout_s: float) -> None:
        timeout = _seconds(timeout_s, "Stop timeout")
        self._stop.set()
        thread = self._thread
        if thread is current_thread():
            raise RuntimeError("A monitor cannot join itself")
        if thread is not None and thread.ident is not None:
            thread.join(timeout)
            if thread.is_alive():
                with self._lock:
                    self._service_error = "Monitor stop timed out; worker and CSV are still owned"
                raise TimeoutError(self._service_error)

    def _poll(self, started: float) -> Sample:
        now, utc = time.monotonic(), datetime.now(UTC)
        status = Status(False, "unknown", "unavailable")
        try:
            status = self._chiller.read_status()
            temperature = self._chiller.read_temperature()
            setpoint = self._chiller.read_setpoint()
            return Sample(utc, now - started, temperature, setpoint, status, sampled_monotonic=now)
        except (OSError, ProtocolError) as exc:
            return Sample(
                utc,
                now - started,
                None,
                None,
                Status(False, status.backend, "poll failed"),
                str(exc) or type(exc).__name__,
                now,
            )

    def _publish(self, sample: Sample) -> None:
        written = False
        if self._writer is not None and self._file is not None:
            try:
                self._writer.writerow(
                    [
                        sample.timestamp_utc.isoformat(),
                        sample.elapsed_s,
                        sample.temperature_c,
                        sample.setpoint_c,
                        sample.status.backend,
                        sample.status.connected,
                        sample.status.detail,
                        sample.error,
                    ]
                )
                self._file.flush()
                written = True
            except (OSError, ValueError) as exc:
                with self._lock:
                    self._logging_error = f"CSV recording stopped: {exc}"
                self._close_csv()
        with self._lock:
            self._history.append(sample)
            self._count += 1
            self._failed += bool(sample.error)
            self._logged += written

    def _run(self, ready: Event) -> None:
        ready.wait()
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                poll_started = time.monotonic()
                self._publish(self._poll(started))
                self._stop.wait(max(0, self.interval_s - (time.monotonic() - poll_started)))
        except BaseException as exc:
            with self._lock:
                self._service_error = f"Monitoring stopped: {type(exc).__name__}: {exc}"
        finally:
            try:
                self._close_csv()
            except BaseException as exc:
                with self._lock:
                    self._logging_error = f"CSV close failed: {type(exc).__name__}: {exc}"
            finally:
                with self._lock:
                    self._running = False
