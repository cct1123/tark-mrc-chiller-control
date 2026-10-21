"""Serial protocols, device ownership, monitoring and CSV recording.

Construction opens nothing. Use TarkChiller as the single owner of one device.
CAL 3300/9300 and ThermoTek T255P protocol details are in docs/protocol.md.
"""

import csv
import logging
import math
import time
from _thread import LockType
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from numbers import Real
from pathlib import Path
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from time import monotonic, perf_counter, sleep
from typing import Any, Literal, TextIO, TypedDict

import serial
from serial.rs485 import RS485Settings

logger = logging.getLogger(__name__)


class _Status(TypedDict):
    """Connection diagnostics as an ordinary dict; no physical safety guarantee."""

    connected: bool
    backend: str
    detail: str


class _AttemptReadings(TypedDict, total=False):
    """Validated fields from the current read attempt, including a failed attempt.

    The controller writes status before polling. T255P.sample() adds detail,
    temperature and target only after validating each field. Recovery replaces
    the entire dict before each attempt, including a reconnect handshake.
    _poll() may record these fields on failure, always with disconnected status
    and an error. They must never be reused as fresh readings or across retries.
    """

    status: _Status
    detail: str
    temperature_c: float
    setpoint_c: float


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
    status: _Status
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


class ProtocolError(RuntimeError):
    """Invalid protocol or device response; never accept its payload."""


class _T255PReadError(ProtocolError):
    """Discarded link response; a read may restart with a fresh handshake."""


class _T255PCommandMismatch(_T255PReadError):
    """Checksum-valid reply to another command; never use its payload."""


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


def _rtu(payload: bytes) -> bytes:
    """Append the CAL guide's CRC-16, low byte first (section 2.6)."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return payload + crc.to_bytes(2, "little")


def _rtu_complete(response: bytes) -> bool:
    if len(response) < 2:
        return False
    function = response[1]
    length = {1: 6, 3: 7, 6: 8}.get(function)
    if function & 0x80:
        length = 5
    if length is None:
        raise ProtocolError(f"Unexpected Modbus function 0x{function:02x}")
    return len(response) >= length


RegisterIO = Callable[[int, int, int], int]
AsciiIO = Callable[[bytes, bytes], bytes]


def _t255p_frame(payload: bytes) -> bytes:
    """Rev C section 16: printable payload, additive checksum and CR."""
    return payload + f"{sum(payload) & 0xFF:02X}\r".encode("ascii")


def _t255p_payload(frame: bytes, prefix: bytes) -> bytes:
    if (
        len(frame) < 5
        or not frame.startswith(prefix)
        or not frame.endswith(b"\r")
        or any(not 32 <= byte <= 126 for byte in frame[:-1])
        or any(byte not in b"0123456789abcdefABCDEF" for byte in frame[-3:-1])
    ):
        raise ProtocolError("Malformed T255P ASCII frame")
    if int(frame[-3:-1], 16) != sum(frame[:-3]) & 0xFF:
        raise ProtocolError("T255P checksum mismatch")
    return frame[1:-3]


class T255P:
    """ThermoTek Rev C water protocol; explicitly select for an identified unit.

    U establishes remote communication and can change AUTO START to RUN. Each
    standalone operation or monitor poll starts with U; no hidden worker runs.
    No identity query exists in the documented subset: selection is human-owned.
    """

    def __init__(self, allow_writes: bool = False) -> None:
        self._allow_writes = allow_writes
        if type(self._allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")

    @staticmethod
    def is_complete(response: bytes) -> bool:
        """Check partial ASCII framing; reply() validates the completed checksum."""
        if response[:1] != b"#" or any(not 32 <= byte <= 126 for byte in response.rstrip(b"\r")):
            raise ProtocolError("Unexpected T255P response framing")
        return response.endswith(b"\r")

    @staticmethod
    def reply(command: bytes, response: bytes) -> bytes:
        """Validate a complete reply and return data after command/error fields."""
        payload = _t255p_payload(response, b"#")
        if len(payload) < 2:
            raise ProtocolError("Malformed T255P command/error fields")
        error = payload[1:2]
        if error != b"0":
            message = {b"1": "checksum", b"2": "bad command", b"3": "qualifier out of bounds"}
            error_type = _T255PReadError if error in (b"1", b"2") else ProtocolError
            raise error_type(
                f"T255P communication error: {message.get(error, 'unknown code')}; "
                f"expected={command!r}, echo={payload[:1]!r}, frame={response!r}"
            )
        if payload[:1] != command:
            raise _T255PCommandMismatch(
                f"T255P command echo mismatch: expected {command!r}, "
                f"received {payload[:1]!r}; frame={response!r}"
            )
        return payload[2:]

    @staticmethod
    def _signed(data: bytes, digits: int) -> float:
        if len(data) != digits + 1 or data[:1] not in (b"+", b"-") or not data[1:].isdigit():
            raise ProtocolError("Malformed T255P signed temperature")
        return int(data) / (100 if digits == 4 else 10)

    @staticmethod
    def _parse_session_state(data: bytes) -> str:
        """Validate U data: mode, summary alarm, chiller-on and dryer-on flags."""
        if len(data) != 4 or data[:1] not in b"0123" or any(c not in b"01" for c in data[1:]):
            raise ProtocolError("Malformed T255P watchdog state")
        return data.decode("ascii")

    @staticmethod
    def _alarms(data: bytes) -> str:
        if len(data) != 6 or any(c not in b"01" for c in data):
            raise ProtocolError("Malformed T255P alarm flags")
        return data.decode("ascii")

    def _parse_target_memory(self, data: bytes) -> float:
        """Validate H0 memory fields and decode the Celsius target; power is ignored."""
        if len(data) != 9 or data[:1] != b"0" or data[5:6] != b"," or not data[6:].isdigit():
            raise ProtocolError("Malformed T255P memory response")
        target = self._signed(data[1:5], 3)
        if not -5 <= target <= 45:
            raise ProtocolError("T255P reported target outside documented -5..45 C range")
        return target

    def verify(self, io: AsciiIO) -> str:
        """Send U; return four characters: mode, alarm, chiller-on, dryer-on."""
        return self._parse_session_state(io(b"U", b""))

    def read_temperature(self, io: AsciiIO) -> float:
        """Read U/J diagnostics, reject a sensor fault, then read I in Celsius."""
        self.verify(io)
        alarms = self._alarms(io(b"J", b""))
        if alarms[3] == "1":  # J flag order: float, high, low, sensor, EEPROM, watchdog.
            raise ProtocolError("T255P sensor alarm; temperature unavailable")
        return self._signed(io(b"I", b""), 4)

    def read_setpoint(self, io: AsciiIO) -> float:
        """Establish remote communication with U, then read target memory H0."""
        self.verify(io)
        return self._parse_target_memory(io(b"H", b"0"))

    def read_status(self, io: AsciiIO) -> str:
        """Format the last U mode/on flags and J alarm flags as diagnostics."""
        mode_code, summary_alarm, chiller_on, dryer_on = tuple(self.verify(io))
        alarms = self._alarms(io(b"J", b""))
        mode = ("AUTO START", "STANDBY", "RUN", "SAFETY DEFAULT")[int(mode_code)]
        names = ("float_switch", "high_temp", "low_temp", "sensor", "eeprom", "watchdog")
        flags = " ".join(f"{name}={flag}" for name, flag in zip(names, alarms, strict=True))
        return (
            f"T255P (human-selected; identity not queried); mode={mode}; "
            f"summary_alarm={summary_alarm} chiller_on={chiller_on} dryer_on={dryer_on}; "
            f"reported alarms: {flags}; flow/leak unknown; last serial reply, not safety proof"
        )

    def sample(
        self, io: AsciiIO, partial: _AttemptReadings | None = None
    ) -> tuple[str, float, float]:
        """Return (detail, temperature_c, setpoint_c) from one U/J/I/H poll.

        If partial is supplied, fill it after validating each field so the caller
        can record historical readings when a later command fails. Its lifetime
        is one attempt; the controller discards it before retrying the whole poll.
        """
        # The cache lives for one poll only. Writes always use fresh diagnostics.
        read = cache(io)
        fields: _AttemptReadings = partial if partial is not None else {}
        fields["detail"] = self.read_status(read)
        fields["temperature_c"] = _reading(self.read_temperature(read))
        fields["setpoint_c"] = _reading(self.read_setpoint(read))
        return fields["detail"], fields["temperature_c"], fields["setpoint_c"]

    def _require_writes(self) -> None:
        if not self._allow_writes:
            raise ProtocolError(
                "T255P writes disabled; enable with T255P(allow_writes=True) after checking setup"
            )

    def set_setpoint(self, value: float, io: AsciiIO) -> None:
        """Check target and fresh U/J state, send M once, then verify H readback."""
        self._require_writes()
        number = _finite(value, "Setpoint")
        if not 5 <= number <= 45:
            raise ProtocolError("T255P water protocol supports targets from 5 to 45 C only")
        if abs(number * 10 - round(number * 10)) > 1e-8:
            raise ProtocolError("T255P targets require 0.1 C resolution; no rounding")
        target = f"{round(number * 10):+04d}".encode("ascii")
        mode, summary_alarm, _chiller_on, _dryer_on = tuple(self.verify(io))
        alarms = self._alarms(io(b"J", b""))
        if mode != "2" or summary_alarm != "0" or "1" in alarms:
            raise ProtocolError("T255P must report RUN with no active alarms before a target write")
        if io(b"M", target) != target:
            raise ProtocolError("T255P target echo mismatch; outcome uncertain")
        if self._parse_target_memory(io(b"H", b"0")) != int(target) / 10:
            raise ProtocolError("T255P target readback mismatch; outcome uncertain")

    def set_mode(self, running: bool, io: AsciiIO) -> None:
        """Check fresh U/J state, send G once, then verify U mode/on readback."""
        self._require_writes()
        mode, summary_alarm, _chiller_on, _dryer_on = tuple(self.verify(io))
        alarms = self._alarms(io(b"J", b""))
        if running and (mode == "3" or summary_alarm != "0" or "1" in alarms):
            raise ProtocolError("RUN refused: safety-default mode or active alarms")
        qualifier = b"1" if running else b"0"
        if io(b"G", qualifier) != qualifier:
            raise ProtocolError("T255P mode echo mismatch; outcome uncertain")
        reported_mode, reported_alarm, reported_on, _dryer_on = tuple(self.verify(io))
        if reported_mode != ("2" if running else "1") or reported_on != qualifier.decode("ascii"):
            raise ProtocolError("T255P mode/chiller-on readback mismatch; outcome uncertain")
        if running and reported_alarm != "0":
            raise ProtocolError("T255P alarm after RUN request; outcome uncertain")


class Cal33xx:
    """Documented CAL 3300/9300 subset; no automatic configuration or reset.

    Reads verify supported family/firmware, RTD input and Celsius units.
    Writes need explicit opt-in and exact expected identity codes from a prior read.
    This driver accepts nonnegative temperatures only; negative wire encoding
    is not specified by the source. Each instance belongs to one TarkChiller.
    """

    def __init__(
        self,
        address: int,
        allow_writes: bool = False,
        expected_model: int | None = None,
        expected_firmware: int | None = None,
    ) -> None:
        self._address = address
        self._allow_writes = allow_writes
        self._expected_model = expected_model
        self._expected_firmware = expected_firmware
        if type(self._address) is not int or not 1 <= self._address <= 247:
            raise ValueError("CAL address must be 1..247; broadcasts are forbidden")
        if type(self._allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")
        for name, allowed in (("expected_model", (1, 2, 3)), ("expected_firmware", (0xFFFF, 1, 2))):
            value = getattr(self, "_" + name)
            if value is not None and (type(value) is not int or value not in allowed):
                raise ValueError(f"Unsupported CAL {name}")
        if self._allow_writes and (self._expected_model is None or self._expected_firmware is None):
            raise ValueError("Writes require expected_model and expected_firmware from read review")

    def verify(self, io: RegisterIO) -> str:
        """Require supported identity, reviewed codes (if supplied), and RTD/Celsius."""
        model, firmware = io(3, 0x04FC, 1), io(3, 0x04FD, 1)
        if model not in (1, 2, 3) or firmware not in (0xFFFF, 1, 2):
            raise ProtocolError(
                f"Unsupported CAL identity: model=0x{model:04x}, firmware=0x{firmware:04x}"
            )
        if (self._expected_model is not None and model != self._expected_model) or (
            self._expected_firmware is not None and firmware != self._expected_firmware
        ):
            raise ProtocolError("CAL identity differs from the reviewed controller")
        self._units(io)
        return f"CAL 3300/9300 model=0x{model:04x} firmware=0x{firmware:04x}"

    @staticmethod
    def _units(io: RegisterIO) -> None:
        if io(3, 0x0198, 1) != 10 or io(3, 0x0199, 1) != 1:
            raise ProtocolError(
                "This CAL driver requires Inpt=RTD and Unit=C; no settings were changed"
            )

    @staticmethod
    def _temperature(raw: int) -> float:
        if raw > 4000:
            raise ProtocolError(
                "Unsupported temperature encoding/range; negative values are not enabled"
            )
        return raw / 10

    def _write_checks(self, io: RegisterIO, value: float) -> int:
        self._units(io)
        low = self._temperature(io(3, 0x0096, 1))
        high = self._temperature(io(3, 0x0094, 1))
        locked, high_resolution = io(1, 0x0028, 1), io(1, 0x002A, 1)
        initialized = io(3, 0x0125, 1) & 2
        display, ramp = io(3, 0x0306, 1), io(3, 0x0305, 1)
        if locked or not initialized or display & 0x3F or ramp & 0x0E:
            raise ProtocolError(
                "CAL is locked, uninitialized, or displaying a special operating state"
            )
        if not 0 <= low < high or not low <= value <= high:
            raise ProtocolError(f"Target outside controller Lo.SC/Hi.SC [{low:g}, {high:g}] C")
        scaled = value * (10 if high_resolution else 1)
        if abs(scaled - round(scaled)) > 1e-8:
            raise ProtocolError(
                "Target must match DISP resolution (0.1 C high / 1 C low); no rounding"
            )
        return round(value * 10)

    def read_temperature(self, io: RegisterIO) -> float:
        """Require RTD/Celsius and no FAIL display before reading PV."""
        self._units(io)
        display = io(3, 0x0306, 1)
        if display & 0x20:  # CAL display bit 5 means FAIL; PV is unavailable.
            raise ProtocolError(
                f"Controller reports FAIL (display=0x{display:02x}); reading unavailable"
            )
        return self._temperature(io(3, 0x001C, 1))

    def read_setpoint(self, io: RegisterIO) -> float:
        """Require RTD/Celsius before interpreting the SP1 register."""
        self._units(io)
        return self._temperature(io(3, 0x007F, 1))

    def read_status(self, io: RegisterIO) -> str:
        """Read identity, configuration and display state for diagnostics."""
        identity = self.verify(io)
        display, ramp = io(3, 0x0306, 1), io(3, 0x0305, 1)
        low, high = self._temperature(io(3, 0x0096, 1)), self._temperature(io(3, 0x0094, 1))
        locked, resolution = io(1, 0x0028, 1), io(1, 0x002A, 1)
        initialized = bool(io(3, 0x0125, 1) & 2)
        message = {
            0: "temperature display",
            1: "PARK",
            2: "ALARM",
            3: "TUNE",
            4: "TUNE ATSP",
            5: "HAND",
            0x23: "TUNE FAIL",
            0x25: "HAND FAIL",
            0x26: "INPT FAIL",
            0x27: "DATA FAIL",
        }.get(display & 0x3F, "unknown display state")
        return (
            f"{identity}; RTD/C; Lo.SC={low:g} Hi.SC={high:g}; SP.LK={locked} "
            f"DISP={'high' if resolution else 'low'} initialized={initialized}; "
            f"{message}; display=0x{display:02x} ramp=0x{ramp:02x}; "
            "flow/level/leak unknown"
        )

    def sample(self, io: RegisterIO) -> tuple[str, float, float]:
        """Read status, temperature and target, sharing checks within this poll."""
        read = cache(io)
        return self.read_status(read), self.read_temperature(read), self.read_setpoint(read)

    def set_setpoint(self, value: float, io: RegisterIO) -> None:
        """Check prerequisites, stage/save SP1 once and verify its readback."""
        if not self._allow_writes:
            raise ProtocolError(
                "CAL writes disabled; complete read-only review before enabling allow_writes"
            )
        target = _finite(value, "Setpoint")
        if not 0 <= target <= 40:
            raise ProtocolError("This CAL driver supports targets from 0 to 40 C only")
        self.verify(io)
        self._write_checks(io, target)
        # No retries or finally-unlock: exiting can commit an uncertain staged value.
        try:
            io(6, 0x0300, 5)
            io(6, 0x1500, 0)
            raw = self._write_checks(io, target)  # Recheck after front-panel lock.
            io(6, 0x007F, raw)
            io(6, 0x0300, 6)
            io(6, 0x1600, 0)  # Saves settings and initiates controller restart.
            self._units(io)
            if self._temperature(io(3, 0x007F, 1)) != target:
                raise ProtocolError("CAL setpoint readback differs from request")
        except BaseException as exc:
            exc.add_note(
                "CAL write interrupted: keypad/state may be uncertain. No automatic exit or retry."
            )
            raise


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
        protocol: Cal33xx | T255P | None = None,
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
        self._attempt_readings: _AttemptReadings = {}
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
            (3.0 if isinstance(protocol, T255P) else 1.0) if timeout_s is None else timeout_s,
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
            raise ProtocolError(
                "Device write outcome uncertain; inspect hardware before creating a new session"
            )
        if not isinstance(self._protocol, (Cal33xx, T255P)):
            raise ProtocolError(
                "No protocol configured; pass protocol=Cal33xx(...) or protocol=T255P() "
                "for your identified controller. No port was opened."
            )
        if self._port_connected:
            return
        if isinstance(self._protocol, Cal33xx):
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
                raise ProtocolError("CAL needs documented baud, 8N1/8E1/8O1 and no flow control")
        if isinstance(self._protocol, T255P):
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
                raise ProtocolError("T255P needs RS-232, 9600/8N1 and XON/XOFF only")
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
            if isinstance(self._protocol, Cal33xx):
                self._protocol.verify(self._cal_io)
            elif isinstance(self._protocol, T255P):
                self._protocol.verify(self._t255p_io)
        except BaseException as exc:
            self._close_after_error(exc)
            if not isinstance(exc, Exception) or isinstance(exc, ProtocolError):
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
            raise ProtocolError("Protocol must supply a nonempty byte request")
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
                        raise ProtocolError("Unexpected trailing response bytes")
                    return bytes(response)
            if len(response) >= self.max_response_bytes:
                raise ProtocolError("Response exceeded byte budget")

    def _cal_io(self, function: int, address: int, value: int) -> int:
        assert isinstance(self._protocol, Cal33xx)
        # At least 3.5 characters between frames; one write() supplies each burst.
        bits = (
            1
            + self.settings["bytesize"]
            + (self.settings["parity"] != "N")
            + self.settings["stopbits"]
        )
        sleep(3.5 * bits / self.settings["baudrate"])
        self._serial.reset_input_buffer()
        request = _rtu(
            bytes((self._protocol._address, function))
            + address.to_bytes(2, "big")
            + value.to_bytes(2, "big")
        )
        if function == 6:
            self._write_uncertain = True  # Latched until the whole sequence succeeds.
        response = self._transfer(request, _rtu_complete)
        sleep(3.5 * bits / self.settings["baudrate"])
        if self._serial.in_waiting:
            raise ProtocolError("Unexpected trailing CAL response bytes")
        if _rtu(response[:-2]) != response or response[0] != self._protocol._address:
            raise ProtocolError("CAL reply CRC or slave address mismatch")
        if response[1] == function | 0x80:
            raise ProtocolError(f"CAL Modbus exception {response[2]} for function {function}")
        if response[1] != function:
            raise ProtocolError("CAL reply function mismatch")
        if function == 6:
            if response != request:
                raise ProtocolError("CAL write echo mismatch; outcome unknown")
            return value
        expected_count = 1 if function == 1 else 2
        if response[2] != expected_count:
            raise ProtocolError("CAL reply byte count mismatch")
        raw = int.from_bytes(response[3:-2], "big")
        if function == 1 and raw not in (0, 1):
            raise ProtocolError("CAL coil reply is not a single bit")
        if function == 3 and address & 0x0100 and raw > 255:
            raise ProtocolError("CAL byte register returned a nonzero high byte")
        return raw

    def _t255p_io(self, command: bytes, data: bytes = b"") -> bytes:
        assert isinstance(self._protocol, T255P)
        # Even a short host timeout must not permit another command before 3 s.
        # Preserve this quarantine across close/reopen on this owned endpoint.
        delay = self._t255p_next_command_at - perf_counter()
        if delay > 0:
            cancel = self._cancel
            cancel.wait(delay)
            self._check_cancel(cancel)
        self._serial.reset_input_buffer()
        request = _t255p_frame(b"." + command + data)
        logger.debug("T255P TX %r", request)
        self._t255p_next_command_at = perf_counter() + 3.0
        if command in (b"M", b"G"):
            self._write_uncertain = True
        response = self._transfer(request, self._protocol.is_complete)
        logger.debug("T255P RX %r", response)
        try:
            result = self._protocol.reply(command, response)
        except ProtocolError:
            logger.error("T255P rejected transaction: TX %r RX %r", request, response)
            raise
        if self._serial.in_waiting:
            raise ProtocolError("Unexpected trailing T255P response bytes")
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
                # Transport latches before sending; clear only after full readback.
                self._write_uncertain = False
        except BaseException as exc:
            self._close_after_error(exc)
            if self._write_uncertain and isinstance(exc, Exception):
                device = "T255P" if isinstance(self._protocol, T255P) else "CAL"
                raise ProtocolError(
                    f"{device} write outcome uncertain; target/control state may have changed. "
                    "No retry or automatic recovery write. Human inspection required. " + str(exc)
                ) from exc
            if not isinstance(exc, Exception) or isinstance(exc, ProtocolError):
                raise
            raise _io_error(exc) from exc

    def _status(self, detail: object) -> _Status:
        if not isinstance(detail, str):
            raise ProtocolError("Protocol returned invalid hardware status")
        simulated = getattr(self._serial, "_simulated", False) is True
        return {
            "connected": True,
            "backend": "simulator" if simulated else "hardware",
            "detail": ("Simulation; " if simulated else "") + detail,
        }

    def _read_sample(self) -> tuple[_Status, float, float]:
        with self._device_operation():
            self._attempt_readings = {"status": self._status("poll failed")}
            if isinstance(self._protocol, Cal33xx):
                detail, temperature, setpoint = self._protocol.sample(self._cal_io)
            else:
                assert isinstance(self._protocol, T255P)
                detail, temperature, setpoint = self._protocol.sample(
                    self._t255p_io, self._attempt_readings
                )
            return self._status(detail), _reading(temperature), _reading(setpoint)

    def _read_temperature(self) -> float:
        with self._device_operation():
            if isinstance(self._protocol, Cal33xx):
                return _reading(self._protocol.read_temperature(self._cal_io))
            assert isinstance(self._protocol, T255P)
            return _reading(self._protocol.read_temperature(self._t255p_io))

    def _read_setpoint(self) -> float:
        with self._device_operation():
            if isinstance(self._protocol, Cal33xx):
                return _reading(self._protocol.read_setpoint(self._cal_io))
            assert isinstance(self._protocol, T255P)
            return _reading(self._protocol.read_setpoint(self._t255p_io))

    def _write_setpoint(self, value: float) -> None:
        with self._device_operation(writing=True):
            if isinstance(self._protocol, Cal33xx):
                self._protocol.set_setpoint(value, self._cal_io)
            else:
                assert isinstance(self._protocol, T255P)
                self._protocol.set_setpoint(value, self._t255p_io)

    def _read_status(self) -> _Status:
        with self._device_operation():
            if isinstance(self._protocol, Cal33xx):
                return self._status(self._protocol.read_status(self._cal_io))
            assert isinstance(self._protocol, T255P)
            return self._status(self._protocol.read_status(self._t255p_io))

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
            except (OSError, ProtocolError) as exc:
                self._check_cancel(token)
                if self._fault:
                    raise
                protocol_error = isinstance(exc, ProtocolError)
                retryable = isinstance(exc, (OSError, _T255PReadError))
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

    def read_temperature(self) -> float:
        """Query a validated Celsius temperature; raise if unavailable.

        This waits for serial access and may recover reads when configured.
        Sensor/protocol failures are never returned as numeric measurements.
        """
        with self._io_lock:
            return self._read_with_recovery(
                lambda: _reading(self._read_temperature()), self._cancel
            )

    def read_setpoint(self) -> float:
        """Query the reported Celsius target, not the measured liquid temperature."""
        with self._io_lock:
            return self._read_with_recovery(lambda: _reading(self._read_setpoint()), self._cancel)

    def set_setpoint(self, value_c: object) -> None:
        """Validate, write once and verify a Celsius target; return None on success.

        Requires a connected, write-enabled protocol. CAL saves and restarts its
        controller. Failure after a write starts latches an uncertain outcome;
        inspect hardware before another session. This method never retries writes.
        """
        value = _finite(value_c, "Setpoint (Celsius)")
        low, high = self._setpoint_range
        if not low <= value <= high:
            raise ValueError(
                f"Setpoint {value:g} Celsius is outside {self.coolant} [{low:g}, {high:g}]"
            )
        with self._intent_lock:
            token = self._cancel
        with self._io_lock:
            self._check_cancel(token)
            self._require_connected()
            try:
                self._write_setpoint(value)
            except ProtocolError as exc:
                self._suspend_session(f"Protocol failure; recovery suspended: {exc}", exc)
                raise

    @property
    def supports_mode_control(self) -> bool:
        """Whether the selected protocol implements RUN/STANDBY; no device I/O."""
        return isinstance(self._protocol, T255P)

    def run(self) -> None:
        """T255P: start pump and temperature regulation at the existing target.

        Requires a connected, write-enabled session. Check the target and prepare
        the loop first. This writes once and verifies readback; an uncertain
        outcome latches the session. It does not start monitoring.
        """
        self._set_mode("run")

    def standby(self) -> None:
        """T255P: stop pump and temperature regulation; monitoring can continue.

        Requires a connected, write-enabled session. This writes once and verifies
        readback; an uncertain outcome latches the session. Reported alarms do not
        prevent this request. Disconnect alone does not perform this operation.
        """
        self._set_mode("standby")

    def _set_mode(self, operation: Literal["run", "standby"]) -> None:
        with self._intent_lock:
            token = self._cancel
        with self._io_lock:
            self._check_cancel(token)
            self._require_connected()
            if not self.supports_mode_control:
                raise ProtocolError("RUN/STANDBY control is supported only for T255P")
            try:
                with self._device_operation(writing=True):
                    assert isinstance(self._protocol, T255P)
                    self._protocol.set_mode(operation == "run", self._t255p_io)
            except ProtocolError as exc:
                self._suspend_session(f"Protocol failure; recovery suspended: {exc}", exc)
                raise

    def read_status(self) -> _Status:
        """Return connected/backend/detail diagnostics, querying an active device.

        A disconnected or suspended session returns disconnected diagnostics.
        An attempted query can raise. Alarm flags do not prove physical flow.
        """
        with self._io_lock:
            if self._cancel.is_set() or self._fault:
                return {
                    "connected": False,
                    "backend": "unknown",
                    "detail": self._fault or "disconnected",
                }

            def read() -> _Status:
                status = self._read_status()
                if not isinstance(status, dict) or (
                    type(status.get("connected")) is not bool
                    or not isinstance(status.get("backend"), str)
                    or (not status["backend"].strip())
                    or (not isinstance(status.get("detail"), str))
                ):
                    raise ProtocolError("Controller returned an invalid status")
                return {
                    "connected": status["connected"],
                    "backend": status["backend"],
                    "detail": status["detail"],
                }

            return self._read_with_recovery(read, self._cancel)

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

        This is acquisition state, not the equipment mode controlled by run().
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
                    {**sample, "status": sample["status"].copy()} for sample in self._history
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
        status: _Status = {"connected": False, "backend": backend, "detail": "unavailable"}
        error = ""
        temperature: float | None = None
        setpoint: float | None = None
        try:
            with self._io_lock:
                status, temperature, setpoint = self._read_with_recovery(
                    self._read_sample, self._cancel, monitoring=True
                )
        except (OSError, ProtocolError) as exc:
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
