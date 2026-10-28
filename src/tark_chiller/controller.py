"""Serial ownership, connection recovery, monitoring and CSV recording.

Construction opens nothing. Use TarkChiller as the single owner of one device.
CAL 3300/9300 and ThermoTek T255P protocol details are in docs/protocol.md.
"""

import csv
import logging
import time
from _thread import LockType
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from time import monotonic, perf_counter, sleep
from typing import Any, Literal, TextIO, TypedDict

import serial
from serial.rs485 import RS485Settings

from . import protocols

logger = logging.getLogger(__name__)


class _Sample(TypedDict):
    """One poll's readings and timing; unavailable readings are None.

    timestamp_utc and elapsed_s describe poll start. The two monotonic times
    delimit the whole poll, including recovery, rather than each sensor reading.
    A failed poll may retain validated fields from its final attempt.
    """

    timestamp_utc: datetime
    elapsed_s: float
    temperature_c: float | None
    setpoint_c: float | None
    status: protocols._Status
    error: str
    sampled_monotonic: float
    completed_monotonic: float


class _Snapshot(TypedDict):
    """Detached history and worker state; TypedDict adds editor hints, no wrapper.

    service_error is a worker/shutdown failure; recovery_error is a temporary
    retry reason; logging_error is independent of acquisition. logging_enabled
    means a file handle is owned, including when closing that file has failed.
    """

    history: list[_Sample]
    running: bool
    service_error: str
    recovery_error: str
    poll_in_progress: bool
    logging_error: str
    logging_enabled: bool
    logged_samples: int
    sample_count: int
    failed_samples: int


def _seconds(value: object, name: str, *, allow_zero: bool = False) -> float:
    seconds = protocols._finite(value, name)
    minimum_ok = seconds >= 0 if allow_zero else seconds > 0
    if not minimum_ok or seconds > TIMEOUT_MAX:
        limit = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {limit} and at most {TIMEOUT_MAX:g} seconds")
    return seconds


@contextmanager
def _locked_until(lock: LockType, deadline: float | None = None) -> Iterator[None]:
    if deadline is None:
        acquired = lock.acquire()
    else:
        remaining_s = min(TIMEOUT_MAX, max(0.0, deadline - monotonic()))
        acquired = lock.acquire(timeout=remaining_s)
    if not acquired:
        raise TimeoutError(
            "Shutdown timed out waiting for access; resources remain owned. Retry cleanup."
        )
    try:
        yield
    finally:
        lock.release()


def _validate_serial(settings: dict[str, Any]) -> dict[str, Any]:
    required = {
        "port",
        "baudrate",
        "bytesize",
        "parity",
        "stopbits",
        "xonxoff",
        "rtscts",
        "dsrdtr",
        "rts",
        "dtr",
    }
    if not isinstance(settings, dict) or settings.keys() != required:
        raise ValueError(
            f"Serial settings must explicitly supply exactly: {', '.join(sorted(required))}"
        )
    settings = settings.copy()
    if not isinstance(settings["port"], str) or not settings["port"].strip():
        raise ValueError("port must be explicitly named")
    if type(settings["baudrate"]) is not int or settings["baudrate"] <= 0:
        raise ValueError("baudrate must be a positive integer")
    if type(settings["bytesize"]) is not int or settings["bytesize"] not in (5, 6, 7, 8):
        raise ValueError("bytesize must be 5, 6, 7, or 8")
    if settings["parity"] not in ("N", "E", "O", "M", "S"):
        raise ValueError("unsupported parity")
    if isinstance(settings["stopbits"], bool) or settings["stopbits"] not in (1, 1.5, 2):
        raise ValueError("stopbits must be 1, 1.5, or 2")
    for name in ("xonxoff", "rtscts", "dsrdtr", "rts", "dtr"):
        if type(settings[name]) is not bool:
            raise ValueError(f"{name} must be an explicit boolean")
    return settings


def _validate_rs485(mode: dict[str, Any] | None) -> dict[str, Any] | None:
    if mode is None:
        return None
    required = {
        "rts_level_for_tx",
        "rts_level_for_rx",
        "loopback",
        "delay_before_tx",
        "delay_before_rx",
    }
    if not isinstance(mode, dict) or mode.keys() != required:
        raise ValueError(
            f"RS-485 settings must explicitly supply exactly: {', '.join(sorted(required))}"
        )
    mode = mode.copy()
    for name in ("rts_level_for_tx", "rts_level_for_rx", "loopback"):
        if type(mode[name]) is not bool:
            raise ValueError(f"{name} must be an explicit boolean")
    for name in ("delay_before_tx", "delay_before_rx"):
        if mode[name] is not None:
            _seconds(mode[name], name, allow_zero=True)
    return mode


def _io_error(error: Exception) -> OSError:
    if isinstance(error, (TimeoutError, serial.SerialTimeoutException)):
        return TimeoutError(str(error))
    return OSError(f"Serial operation failed: {error}")


class TarkChiller:
    """Own one serial port, with explicit CAL/T255P protocol and optional recording.

    Construction opens nothing. Read recovery is bounded; writes are never replayed.
    Custom Celsius limits require a coolant name and documented source.
    """

    def __init__(
        self,
        settings: dict[str, Any],
        *,
        protocol: protocols.Cal33xx | protocols.T255P | None = None,
        rs485: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_response_bytes: int = 4096,
        serial_factory: Callable[..., Any] | None = None,
        setpoint_range: tuple[float, float] = (2.0, 40.0),
        coolant: str = "distilled water",
        coolant_source: str | None = None,
        reconnect_attempts: int = 0,
        reconnect_delay_s: float = 0.05,
    ) -> None:
        if not isinstance(setpoint_range, tuple) or len(setpoint_range) != 2:
            raise ValueError("setpoint_range must be a (minimum, maximum) Celsius tuple")
        low, high = (protocols._finite(v, "setpoint limit") for v in setpoint_range)
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
        delay = protocols._finite(reconnect_delay_s, "reconnect_delay_s")
        if not 0 < delay <= 60:
            raise ValueError("reconnect_delay_s must be greater than 0 and at most 60")
        self._setpoint_range = (low, high)
        self._coolant = coolant
        self._coolant_source = coolant_source or "MRC150/300 User Manual Rev 13, p7"
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay_s = delay
        # Lock order when nested: monitor -> I/O -> data. Never acquire the
        # I/O lock while holding the data lock: snapshots must stay responsive.
        self._io_lock = Lock()  # Serial access, device faults and write outcomes.
        # Held briefly to replace cancellation events; released before I/O waits.
        self._intent_lock = Lock()
        self._cancel = Event()
        self._cancel.set()
        self._fault = ""
        self._monitor_lock = Lock()  # Serialize monitor start/stop and disconnect.
        self._data_lock = Lock()  # Publish history and errors to snapshot readers.
        self._interval_s = 1.0
        self._history: deque[_Sample] = deque(maxlen=3600)
        self._stop = Event()
        self._thread: Thread | None = None
        self._service_error = ""  # Worker failure; remains visible after it stops.
        self._recovery_error = ""  # Temporary read failure while retrying.
        self._poll_in_progress = False
        # I/O-owned attempt state: protocol writes validated fields; _poll reads
        # them after failure. This dict is replaced before every read/retry.
        self._attempt_readings: protocols._AttemptReadings = {}
        self._logging_error = ""
        self._logged_samples = 0
        self._sample_count = 0
        self._failed_samples = 0
        self._file: TextIO | None = None

        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        self._settings = _validate_serial(settings)
        self._rs485 = _validate_rs485(rs485)
        self._timeout_s = _seconds(
            (3.0 if isinstance(protocol, protocols.T255P) else 1.0)
            if timeout_s is None
            else timeout_s,
            "timeout_s",
        )
        self._max_response_bytes = max_response_bytes
        self._protocol = protocol
        self._serial_factory = serial_factory
        self._serial: Any = None
        self._usable = False
        self._write_uncertain = False
        self._t255p_next_command_at = 0.0

    @property
    def settings(self) -> dict[str, Any]:
        return self._settings.copy()

    @property
    def rs485(self) -> dict[str, Any] | None:
        return self._rs485.copy() if self._rs485 is not None else None

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    @property
    def _reconnect_wait_s(self) -> float:
        """Remaining incomplete-response quarantine; no device access."""
        return max(0.0, self._t255p_next_command_at - perf_counter())

    @property
    def _port_connected(self) -> bool:
        return self._usable and self._serial is not None and bool(self._serial.is_open)

    def _open_port(self) -> None:
        if self._write_uncertain:
            raise protocols.ProtocolError(
                "Device write outcome uncertain; inspect hardware before creating a new session"
            )
        if not isinstance(self._protocol, (protocols.Cal33xx, protocols.T255P)):
            raise protocols.ProtocolError(
                "No protocol configured; pass protocol=Cal33xx(...) or protocol=T255P() "
                "for your identified controller. No port was opened."
            )
        if self._port_connected:
            return
        if isinstance(self._protocol, protocols.Cal33xx):
            s = self._settings
            if (
                s["baudrate"] not in (1200, 2400, 4800, 9600, 19200)
                or s["bytesize"] != 8
                or s["parity"] not in ("N", "E", "O")
                or (s["stopbits"] != 1)
                or s["xonxoff"]
                or s["rtscts"]
                or s["dsrdtr"]
            ):
                raise protocols.ProtocolError(
                    "CAL needs documented baud, 8N1/8E1/8O1 and no flow control"
                )
        if isinstance(self._protocol, protocols.T255P):
            s = self._settings
            if (
                s["baudrate"] != 9600
                or s["bytesize"] != 8
                or s["parity"] != "N"
                or (s["stopbits"] != 1)
                or (not s["xonxoff"])
                or s["rtscts"]
                or s["dsrdtr"]
                or (self.rs485 is not None)
            ):
                raise protocols.ProtocolError("T255P needs RS-232, 9600/8N1 and XON/XOFF only")
        self._close_port()
        try:
            config = self._settings.copy()
            rts = config.pop("rts")
            dtr = config.pop("dtr")
            # Configure once, while closed. On Windows each timeout assignment
            # calls SetCommState as well as SetCommTimeouts, disturbing live traffic.
            config.update(
                port=None, timeout=min(self.timeout_s, 0.05), write_timeout=self.timeout_s
            )
            self._serial = (self._serial_factory or serial.Serial)(**config)
            if self._serial.is_open:
                raise OSError("Serial factory must return an unopened endpoint")
            self._serial.port = self._settings["port"]
            self._serial.rts = rts
            self._serial.dtr = dtr
            if self._rs485 is not None:
                self._serial.rs485_mode = RS485Settings(**self._rs485)
            self._serial.open()
            if not self._serial.is_open:
                raise OSError("Serial endpoint did not open")
            self._usable = True
            if isinstance(self._protocol, protocols.Cal33xx):
                self._protocol.verify(self._cal_io)
            elif isinstance(self._protocol, protocols.T255P):
                self._protocol.get_status(self._t255p_io)
        except BaseException as exc:
            self._close_after_error(exc)
            if not isinstance(exc, Exception) or isinstance(exc, protocols.ProtocolError):
                raise
            raise _io_error(exc) from exc

    def _close_port(self) -> None:
        self._usable = False
        if self._serial is not None:
            try:
                self._serial.close()
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    raise
                raise _io_error(exc) from exc
            self._serial = None

    def _close_after_error(self, error: BaseException) -> None:
        """Try closing the port; attach cleanup failure to the original error."""
        try:
            self._close_port()
        except BaseException as cleanup:
            error.add_note(f"Serial cleanup failed: {cleanup}")

    def _transfer(self, request: bytes, is_complete: Callable[[bytes], bool]) -> bytes:
        deadline = perf_counter() + self.timeout_s
        cancel = self._cancel

        def remaining() -> float:
            budget = deadline - perf_counter()
            if budget <= 0:
                raise TimeoutError("Serial transaction timed out; outcome may be unknown")
            return budget

        if not isinstance(request, bytes) or not request:
            raise protocols.ProtocolError("Protocol must supply a nonempty byte request")
        self._check_cancel(cancel)
        remaining()
        written = self._serial.write(request)
        if type(written) is not int or written != len(request):
            raise OSError("Partial serial write; outcome unknown, not retried")
        response = bytearray()
        while True:
            self._check_cancel(cancel)
            # The fixed short read timeout keeps shutdown responsive. Enforce
            # the whole transaction deadline here without reconfiguring the UART.
            remaining()
            size = max(1, min(self._serial.in_waiting, self.max_response_bytes - len(response)))
            chunk = self._serial.read(size)
            self._check_cancel(cancel)
            remaining()
            if not isinstance(chunk, bytes) or len(chunk) > size:
                raise OSError("Serial read violated requested byte count")
            for index, byte in enumerate(chunk):
                response.append(byte)
                complete = is_complete(bytes(response))
                remaining()
                if complete:
                    if index != len(chunk) - 1:
                        raise protocols.ProtocolError("Unexpected trailing response bytes")
                    return bytes(response)
            if len(response) >= self.max_response_bytes:
                raise protocols.ProtocolError("Response exceeded byte budget")

    def _cal_io(self, function: int, address: int, value: int) -> int:
        assert isinstance(self._protocol, protocols.Cal33xx)
        # At least 3.5 characters between frames; one write() supplies each burst.
        bits = (
            1
            + self.settings["bytesize"]
            + (self.settings["parity"] != "N")
            + self.settings["stopbits"]
        )
        sleep(3.5 * bits / self.settings["baudrate"])
        self._serial.reset_input_buffer()
        request = protocols._rtu(
            bytes((self._protocol._address, function))
            + address.to_bytes(2, "big")
            + value.to_bytes(2, "big")
        )
        if function == 6:
            self._write_uncertain = True  # Latched until the whole sequence succeeds.
        response = self._transfer(request, protocols._rtu_complete)
        sleep(3.5 * bits / self.settings["baudrate"])
        if self._serial.in_waiting:
            raise protocols.ProtocolError("Unexpected trailing CAL response bytes")
        if protocols._rtu(response[:-2]) != response or response[0] != self._protocol._address:
            raise protocols.ProtocolError("CAL reply CRC or slave address mismatch")
        if response[1] == function | 0x80:
            raise protocols.ProtocolError(
                f"CAL Modbus exception {response[2]} for function {function}"
            )
        if response[1] != function:
            raise protocols.ProtocolError("CAL reply function mismatch")
        if function == 6:
            if response != request:
                raise protocols.ProtocolError("CAL write echo mismatch; outcome unknown")
            return value
        expected_count = 1 if function == 1 else 2
        if response[2] != expected_count:
            raise protocols.ProtocolError("CAL reply byte count mismatch")
        raw = int.from_bytes(response[3:-2], "big")
        if function == 1 and raw not in (0, 1):
            raise protocols.ProtocolError("CAL coil reply is not a single bit")
        if function == 3 and address & 0x0100 and raw > 255:
            raise protocols.ProtocolError("CAL byte register returned a nonzero high byte")
        return raw

    def _t255p_io(self, command: bytes, data: bytes = b"") -> bytes:
        assert isinstance(self._protocol, protocols.T255P)
        # Even a short host timeout must not permit another command before 3 s.
        # Preserve this quarantine across close/reopen on this owned endpoint.
        delay = self._t255p_next_command_at - perf_counter()
        if delay > 0:
            cancel = self._cancel
            cancel.wait(delay)
            self._check_cancel(cancel)
        self._serial.reset_input_buffer()
        request = protocols._t255p_frame(b"." + command + data)
        logger.debug("T255P TX %r", request)
        self._t255p_next_command_at = perf_counter() + 3.0
        if command in (b"M", b"G", b"O"):
            self._write_uncertain = True
        response = self._transfer(request, self._protocol.is_complete)
        logger.debug("T255P RX %r", response)
        try:
            result = self._protocol.reply(command, response)
        except protocols.ProtocolError:
            logger.error("T255P rejected transaction: TX %r RX %r", request, response)
            raise
        if self._serial.in_waiting:
            raise protocols.ProtocolError("Unexpected trailing T255P response bytes")
        self._t255p_next_command_at = 0.0
        return result

    @contextmanager
    def _device_operation(self, *, writing: bool = False) -> Iterator[None]:
        """Close failed I/O and preserve uncertain writes; caller holds the I/O lock."""
        if not self._port_connected:
            raise ConnectionError("Serial device is disconnected")
        try:
            yield
            if writing:
                # Clear after the command acknowledgement or workflow verification.
                self._write_uncertain = False
        except BaseException as exc:
            self._close_after_error(exc)
            if self._write_uncertain and isinstance(exc, Exception):
                device = "T255P" if isinstance(self._protocol, protocols.T255P) else "CAL"
                raise protocols.ProtocolError(
                    f"{device} write outcome uncertain; target/control state may have changed. "
                    "No retry or automatic recovery write. Human inspection required. " + str(exc)
                ) from exc
            if not isinstance(exc, Exception) or isinstance(exc, protocols.ProtocolError):
                raise
            raise _io_error(exc) from exc

    def _status(self, detail: object) -> protocols._Status:
        fields = detail if isinstance(detail, dict) else {}
        text = fields.get("detail") if fields else detail
        if not isinstance(text, str):
            raise protocols.ProtocolError("Protocol returned invalid hardware status")
        simulated = getattr(self._serial, "_simulated", False) is True
        return {
            **fields,
            "connected": True,
            "backend": "simulator" if simulated else "hardware",
            "detail": ("Simulation; " if simulated else "") + text,
        }

    def _read_sample(self) -> tuple[protocols._Status, float, float]:
        detail: str | protocols._DeviceStatus
        with self._device_operation():
            self._attempt_readings = {"status": self._status("poll failed")}
            if isinstance(self._protocol, protocols.Cal33xx):
                detail, temperature, setpoint = self._protocol.sample(self._cal_io)
            else:
                assert isinstance(self._protocol, protocols.T255P)
                detail, temperature, setpoint = self._protocol.sample(
                    self._t255p_io, self._attempt_readings
                )
            return (
                self._status(detail),
                protocols._reading(temperature),
                protocols._reading(setpoint),
            )

    def _call_protocol(
        self,
        operation: Literal["read_temperature", "read_setpoint", "collect_status", "apply_setpoint"],
        *args: float,
    ) -> float | protocols._Status | None:
        """One adapter for protocol calls; caller owns serial access and recovery."""
        writing = operation == "apply_setpoint"
        with self._device_operation(writing=writing):
            assert isinstance(self._protocol, (protocols.Cal33xx, protocols.T255P))
            io = self._cal_io if isinstance(self._protocol, protocols.Cal33xx) else self._t255p_io
            result = getattr(self._protocol, operation)(*args, io)
            return self._status(result) if operation == "collect_status" else result

    @contextmanager
    def _control_operation(self, *, t255p_only: bool = False) -> Iterator[None]:
        """Serialize an explicit write and suspend the session on protocol failure."""
        with self._intent_lock:
            token = self._cancel
        with self._io_lock:
            self._check_cancel(token)
            self._require_connected()
            if t255p_only and not isinstance(self._protocol, protocols.T255P):
                raise protocols.ProtocolError("This control is supported only for T255P")
            try:
                yield
            except protocols.ProtocolError as exc:
                self._suspend_session(f"Protocol failure; recovery suspended: {exc}", exc)
                raise

    @property
    def setpoint_range(self) -> tuple[float, float]:
        return self._setpoint_range

    @property
    def coolant(self) -> str:
        return self._coolant

    @property
    def coolant_source(self) -> str:
        return self._coolant_source

    def _replace_connection_request(
        self, connected: bool, *, deadline: float | None = None
    ) -> Event:
        """Invalidate older connection requests and return this request's event.

        Each request keeps its own event. Setting the old event prevents a
        queued write or recovery attempt from crossing a disconnect/reconnect.
        """
        with _locked_until(self._intent_lock, deadline):
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
        if self._fault or not self._port_connected:
            raise ConnectionError(self._fault or "TarkChiller is disconnected")

    def connect(self) -> None:
        """Open and verify the port, using the configured read recovery budget.

        T255P sends U, which can start an AUTO START unit. A failed operation
        raises; an uncertain previous write remains blocked even after reconnect.
        """
        token = self._replace_connection_request(connected=True)
        with self._io_lock:
            self._check_cancel(token)
            self._fault = ""
            self._read_with_recovery(lambda: self._open(token), token, connecting=True)

    def _open(self, token: Event) -> None:
        # Protocol recovery may need a quiet period. Make that wait cancellable
        # before opening/taking control, rather than sleeping inside a handshake.
        token.wait(self._reconnect_wait_s)
        self._check_cancel(token)
        self._open_port()
        if not self._port_connected:
            raise ConnectionError("Serial port did not establish a connection")

    def disconnect(self, timeout_s: float = 5.0) -> None:
        """Cancel recovery and close resources; one budget covers locks and joining."""
        deadline = monotonic() + _seconds(timeout_s, "Disconnect timeout")
        token = self._replace_connection_request(connected=False, deadline=deadline)
        with _locked_until(self._monitor_lock, deadline):
            self._stop.set()
            try:
                with _locked_until(self._io_lock, deadline):
                    if token is self._cancel:
                        self._close_port()
            except BaseException as error:
                try:
                    self._stop_and_join_monitor(max(0.0, deadline - monotonic()))
                except BaseException as cleanup:
                    error.add_note(f"Monitor cleanup failed: {type(cleanup).__name__}: {cleanup}")
                raise
            self._stop_and_join_monitor(max(0.0, deadline - monotonic()))

    @property
    def is_connected(self) -> bool:
        """Report usable transport state; this does not query the device."""
        with self._io_lock:
            return not self._cancel.is_set() and not self._fault and self._port_connected

    def _suspend_session(self, message: str, error: BaseException) -> None:
        """Block device use with a fault, clear retry state and try closing the port."""
        self._fault = message
        with self._data_lock:
            self._recovery_error = ""
        try:
            self._close_port()
        except BaseException as cleanup:
            error.add_note(str(cleanup))

    def _read_with_recovery[T](
        self,
        operation: Callable[[], T],
        token: Event,
        *,
        connecting: bool = False,
        monitoring: bool = False,
    ) -> T:
        """Run a read/connect operation with bounded recovery under the I/O lock.

        Only I/O failures and selected T255P link errors may retry. Fatal protocol
        faults suspend the session. Monitoring may try again next poll after an
        exhausted retry budget; synchronous calls require explicit reconnect.
        Writes never enter this loop because a lost reply may hide an applied write.
        """
        for attempt in range(self._reconnect_attempts + 1):
            try:
                # Reset before opening, not just before sampling: a failed
                # recovery handshake must not expose the previous attempt's data.
                self._attempt_readings = {}
                self._check_cancel(token)
                if self._fault:
                    raise ConnectionError(self._fault)
                if attempt:
                    self._close_port()
                    token.wait(self._reconnect_delay_s)
                    self._check_cancel(token)
                    if not connecting:
                        self._open(token)
                        self._check_cancel(token)
                elif not connecting:
                    if monitoring and self._reconnect_attempts and not self._port_connected:
                        self._open(token)
                    else:
                        self._require_connected()
                result = operation()
                self._check_cancel(token)
                with self._data_lock:
                    self._recovery_error = ""
                return result
            except (OSError, protocols.ProtocolError) as exc:
                self._check_cancel(token)
                if self._fault:
                    raise
                protocol_error = isinstance(exc, protocols.ProtocolError)
                retryable = isinstance(exc, (OSError, protocols._T255PReadError))
                if protocol_error and (not retryable or not self._reconnect_attempts):
                    self._suspend_session(f"Protocol failure; recovery suspended: {exc}", exc)
                    raise
                if self._reconnect_attempts == 0:
                    raise
                if attempt == self._reconnect_attempts:
                    if monitoring and retryable:
                        # Record this failed poll, then try again on the next
                        # monitoring interval. Writes and fatal protocol faults
                        # never enter this path. Keep ownership and cancellation.
                        self._close_after_error(exc)
                        raise
                    self._suspend_session(
                        f"Recovery exhausted: {exc}; call connect() explicitly", exc
                    )
                    if protocol_error:
                        raise
                    raise ConnectionError(self._fault) from exc
                logger.warning(
                    "Read recovery %d/%d: %s", attempt + 1, self._reconnect_attempts, exc
                )
                with self._data_lock:
                    self._recovery_error = str(exc)
        raise AssertionError("Unreachable recovery state")

    def _t255p_command[T](
        self, command: Callable[[protocols.T255P, protocols.AsciiIO], T], *, writing: bool = False
    ) -> T:
        """One exchange on an existing connection; no recovery or handshake."""
        if not isinstance(self._protocol, protocols.T255P):
            raise protocols.ProtocolError("Single-command methods are supported only for T255P")
        with self._control_operation(t255p_only=True):
            with self._device_operation(writing=writing):
                return command(self._protocol, self._t255p_io)

    # T255P command APIs. set_mode() additionally owns state checks and readback.

    def get_temperature(self) -> float:
        """I only: decode manifold Celsius; does not query sensor alarm flags."""
        return self._t255p_command(protocols.T255P.get_temperature)

    def get_memory(self) -> protocols._Memory:
        """H0 only: return setpoint_c and raw read-only max_power (not watts)."""
        return self._t255p_command(protocols.T255P.get_memory)

    def get_alarms(self) -> protocols._Alarms:
        """J only: return six named boolean alarm flags."""
        return self._t255p_command(protocols.T255P.get_alarms)

    def get_status(self) -> protocols._Status:
        """U only: return structured mode/summary/on fields and connection detail.

        Detailed alarm flags are a separate J query: get_alarms(). Use
        collect_status() when a combined U/J workflow is wanted.
        """
        return self._status(self._t255p_command(protocols.T255P.get_status))

    def _validate_setpoint(self, value_c: object) -> float:
        value = protocols._finite(value_c, "Setpoint (Celsius)")
        low, high = self._setpoint_range
        if not low <= value <= high:
            raise ValueError(
                f"Setpoint {value:g} Celsius is outside {self.coolant} [{low:g}, {high:g}]"
            )
        return value

    def set_setpoint(self, value_c: object) -> None:
        """M only: validate target and its acknowledgement echo; no H readback.

        Requires write opt-in and an established session. Failure during M
        latches an uncertain write. Success confirms the echo, not thermal state.
        """
        value = self._validate_setpoint(value_c)
        self._t255p_command(lambda protocol, io: protocol.set_setpoint(value, io), writing=True)

    def set_sensor_mode(self, mode: protocols._SensorMode) -> None:
        """O only: select INTERNAL/EXTERNAL sensing and check its qualifier echo.

        Requires write opt-in and a prepared sensor configuration. There is no
        documented sensor-mode query or independent readback. Never replayed.
        """
        if mode not in ("INTERNAL", "EXTERNAL"):
            raise ValueError("Sensor mode must be INTERNAL or EXTERNAL")
        self._t255p_command(lambda protocol, io: protocol.set_sensor_mode(mode, io), writing=True)

    def set_mode(self, mode: Literal["RUN", "STANDBY"]) -> protocols._Status:
        """T255P: skip G if already satisfied, else verify a U/G/U workflow.

        Returns observed status and mode_command_sent. AUTO START with chiller-on
        satisfies a RUN request without relabeling the observed mode. This method
        handles the requested state as one job: U, optional G, then U readback.
        """
        if mode not in ("RUN", "STANDBY"):
            raise ValueError("Mode must be RUN or STANDBY")
        with self._control_operation(t255p_only=True):
            assert isinstance(self._protocol, protocols.T255P)
            with self._device_operation(writing=True):
                return self._status(self._protocol.set_mode(mode, self._t255p_io))

    @property
    def supports_mode_control(self) -> bool:
        """Whether the selected protocol is T255P; no I/O."""
        return isinstance(self._protocol, protocols.T255P)

    # Workflows: explicit coordination, verification and bounded read recovery.

    def read_temperature(self) -> float:
        """Checked read workflow: T255P U/J/I; CAL verifies units/display/PV.

        Sensor faults invalidate the measurement. Configured read recovery can
        reconnect, handshake and restart the whole workflow.
        """
        with self._io_lock:
            return self._read_with_recovery(
                lambda: protocols._reading(self._call_protocol("read_temperature")), self._cancel
            )

    def read_setpoint(self) -> float:
        """Read workflow: T255P U/H0; CAL verifies units before reading SP1."""
        with self._io_lock:
            return self._read_with_recovery(
                lambda: protocols._reading(self._call_protocol("read_setpoint")), self._cancel
            )

    def collect_status(self) -> protocols._Status:
        """Collect status diagnostics: T255P U/J; CAL identity/configuration/state.

        Successful T255P results contain mode, mode_code, summary_alarm,
        chiller_on, dryer_on and six named booleans in alarms. Unavailable status
        omits these fields. Returned nested dictionaries are detached.
        """
        with self._io_lock:
            if self._cancel.is_set() or self._fault:
                return {
                    "connected": False,
                    "backend": "unknown",
                    "detail": self._fault or "disconnected",
                }

            def read() -> protocols._Status:
                status = self._call_protocol("collect_status")
                if not isinstance(status, dict) or (
                    type(status.get("connected")) is not bool
                    or not isinstance(status.get("backend"), str)
                    or (not status["backend"].strip())
                    or (not isinstance(status.get("detail"), str))
                ):
                    raise protocols.ProtocolError("Controller returned an invalid status")
                return deepcopy(status)

            return self._read_with_recovery(read, self._cancel)

    def apply_setpoint(self, value_c: object) -> None:
        """Verified target workflow: T255P U/M/H0; CAL stage/save/restart/readback.

        The complete workflow owns serial access. Writes are never replayed;
        failure after starting the write latches an uncertain outcome.
        """
        value = self._validate_setpoint(value_c)
        with self._control_operation():
            self._call_protocol("apply_setpoint", value)

    def start_monitoring(
        self,
        *,
        interval_s: float = 1.0,
        csv_path: str | Path | None = None,
        history_size: int = 3600,
    ) -> None:
        """Start one polling worker on a connected controller; return None.

        Reset history and counters for this run. If csv_path is supplied, create
        a new file and refuse an existing path. Polling/recording errors are
        reported through snapshot(); starting a second worker raises RuntimeError.
        """
        interval = _seconds(interval_s, "Sampling interval")
        if type(history_size) is not int or history_size <= 0:
            raise ValueError("history_size must be a positive integer")
        with self._monitor_lock:
            with self._io_lock:
                self._require_connected()
            if self.running:
                raise RuntimeError("Monitoring already started; stop it before another run")
            self._close_csv()  # A failed close must finish before opening a replacement.
            stream = None
            if csv_path is not None:
                path = Path(csv_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = path.open("x", newline="", encoding="utf-8")
                self._file = stream  # Retain ownership even if header/cleanup fails.
                try:
                    csv.writer(stream).writerow(
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
                    stream.flush()
                except BaseException as error:
                    try:
                        self._close_csv()
                    except BaseException as cleanup:
                        error.add_note(f"CSV cleanup failed: {cleanup}")
                    raise
            with self._data_lock:
                self._file = stream
                self._interval_s = interval
                self._history = deque(maxlen=history_size)
                self._stop.clear()
                self._thread = None
                self._service_error = self._logging_error = ""
                self._recovery_error = ""
                self._poll_in_progress = False
                self._logged_samples = self._sample_count = self._failed_samples = 0
            self._start()

    def stop_monitoring(self, timeout_s: float = 5.0) -> None:
        """Bound lock wait plus worker join; keep the connection for synchronous use."""
        deadline = monotonic() + _seconds(timeout_s, "Stop timeout")
        with _locked_until(self._monitor_lock, deadline):
            self._stop_and_join_monitor(max(0.0, deadline - monotonic()))

    def __enter__(self) -> "TarkChiller":
        self.connect()
        return self

    def __exit__(self, _type: object, error: BaseException | None, _traceback: object) -> None:
        try:
            self.disconnect()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(
                f"Disconnect cleanup failed: {type(cleanup).__name__}: {cleanup}. Retry disconnect()."
            )

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def running(self) -> bool:
        """Whether the monitor thread is alive, including pending shutdown/cleanup.

        This is acquisition state, not the equipment mode controlled by set_mode().
        """
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> _Snapshot:
        """Copy bounded history, worker state and errors without querying hardware.

        Returned dictionaries are detached from controller state. History entries
        contain Celsius readings (None when unavailable), status and error text,
        UTC poll-start time, and monotonic poll-start/completion times. These are
        sequential readings, not a simultaneous physical measurement.
        """
        with self._data_lock:
            return {
                "history": [
                    {**sample, "status": deepcopy(sample["status"])} for sample in self._history
                ],
                "running": self.running,
                "service_error": self._service_error,
                "recovery_error": self._recovery_error,
                "poll_in_progress": self._poll_in_progress,
                "logging_error": self._logging_error,
                "logging_enabled": self._file is not None,
                "logged_samples": self._logged_samples,
                "sample_count": self._sample_count,
                "failed_samples": self._failed_samples,
            }

    def _close_csv(self) -> None:
        # Keep the handle on failure so a later cleanup call can retry closing it.
        if self._file is not None:
            try:
                self._file.close()
            except BaseException as exc:
                with self._data_lock:
                    message = f"CSV close failed: {exc}"
                    if message not in self._logging_error:
                        self._logging_error = "; ".join(
                            filter(None, (self._logging_error, message))
                        )
                raise
            else:
                with self._data_lock:
                    self._file = None

    def _start(self) -> None:
        # The worker waits at this gate until startup either succeeds or sets
        # _stop after an interruption. It must not poll during partial startup.
        ready: Event | None = None
        try:
            ready = Event()
            self._thread = Thread(
                target=self._run, args=(ready,), name="chiller-monitor", daemon=False
            )
            self._thread.start()
        except BaseException as error:
            self._stop.set()
            if self._thread is None or self._thread.ident is None:
                try:
                    self._close_csv()
                except BaseException as cleanup:
                    error.add_note(f"CSV cleanup failed: {cleanup}")
            raise
        finally:
            if ready is not None:
                ready.set()

    def _stop_and_join_monitor(self, timeout_s: float) -> None:
        """Request monitor stop, join it, then close CSV; retain resources on failure."""
        timeout = _seconds(timeout_s, "Stop timeout", allow_zero=True)
        self._stop.set()
        thread = self._thread
        if thread is current_thread():
            raise RuntimeError("A monitor cannot join itself")
        if thread is not None and thread.ident is not None:
            thread.join(timeout)
            if thread.is_alive():
                with self._data_lock:
                    self._service_error = "Monitor stop timed out; worker and CSV are still owned"
                raise TimeoutError(self._service_error)
        self._close_csv()

    def _poll(self, started: float) -> _Sample:
        sampled_monotonic = time.monotonic()
        timestamp_utc = datetime.now(UTC)
        with self._data_lock:
            self._poll_in_progress = True
            backend = self._history[-1]["status"]["backend"] if self._history else "unknown"
        status: protocols._Status = {
            "connected": False,
            "backend": backend,
            "detail": "unavailable",
        }
        error = ""
        temperature: float | None = None
        setpoint: float | None = None
        try:
            with self._io_lock:
                status, temperature, setpoint = self._read_with_recovery(
                    self._read_sample, self._cancel, monitoring=True
                )
        except (OSError, protocols.ProtocolError) as exc:
            # Preserve only fields validated in the final attempt. Error and
            # disconnected status prevent consumers from presenting them as live.
            temperature = self._attempt_readings.get("temperature_c")
            setpoint = self._attempt_readings.get("setpoint_c")
            partial_status = self._attempt_readings.get("status", status)
            status = {
                "connected": False,
                "backend": partial_status["backend"],
                "detail": "poll failed",
            }
            error = str(exc) or type(exc).__name__
        finally:
            completed_monotonic = time.monotonic()
            with self._data_lock:
                self._poll_in_progress = False
                self._recovery_error = ""
        return {
            "timestamp_utc": timestamp_utc,
            "elapsed_s": sampled_monotonic - started,
            "temperature_c": temperature,
            "setpoint_c": setpoint,
            "status": status,
            "error": error,
            "sampled_monotonic": sampled_monotonic,
            "completed_monotonic": completed_monotonic,
        }

    def _record_and_publish_sample(self, sample: _Sample) -> None:
        """Write/flush CSV if enabled, then publish history and counters.

        Recording failure is reported separately and does not discard the sample.
        Filesystem operations stay outside the data lock so snapshots remain usable.
        """
        row_written = False
        if self._file is not None and not self._logging_error:
            try:
                csv.writer(self._file).writerow(
                    [
                        sample["timestamp_utc"].isoformat(),
                        sample["elapsed_s"],
                        sample["temperature_c"],
                        sample["setpoint_c"],
                        sample["status"]["backend"],
                        sample["status"]["connected"],
                        sample["status"]["detail"],
                        sample["error"],
                    ]
                )
                self._file.flush()
                row_written = True
            except (OSError, ValueError) as exc:
                with self._data_lock:
                    self._logging_error = f"CSV recording stopped: {exc}"
                try:
                    self._close_csv()
                except BaseException as cleanup:
                    exc.add_note(f"CSV cleanup failed: {cleanup}")
        with self._data_lock:
            self._history.append(sample)
            self._sample_count += 1
            if sample["error"]:
                self._failed_samples += 1
            if row_written:
                self._logged_samples += 1

    def _run(self, ready: Event) -> None:
        ready.wait()
        started = time.monotonic()
        try:
            while not self._stop.is_set():
                poll_started = time.monotonic()
                sample = self._poll(started)
                self._record_and_publish_sample(sample)
                if sample["error"]:
                    with self._io_lock:
                        fault = self._fault
                    if fault:
                        with self._data_lock:
                            self._service_error = f"Monitoring stopped: {fault}"
                        break
                poll_duration_s = time.monotonic() - poll_started
                remaining_interval_s = max(0, self.interval_s - poll_duration_s)
                self._stop.wait(remaining_interval_s)
        except BaseException as exc:
            with self._data_lock:
                self._service_error = f"Monitoring stopped: {type(exc).__name__}: {exc}"
        finally:
            try:
                self._close_csv()
            except BaseException as exc:
                with self._data_lock:
                    if not self._logging_error:
                        self._logging_error = f"CSV close failed: {type(exc).__name__}: {exc}"
