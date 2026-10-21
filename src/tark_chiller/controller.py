"""Serial protocols, device ownership, monitoring and CSV recording.

Construction opens nothing. Use TarkChiller as the single owner of one device.
CAL 3300/9300 and ThermoTek T255P protocol details are in docs/protocol.md.
"""

import csv
import math
import time
from _thread import LockType
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path
from threading import TIMEOUT_MAX, Event, Lock, Thread, current_thread
from time import monotonic, perf_counter, sleep
from typing import Any, Literal, TextIO, cast


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


@contextmanager
def _locked_until(lock: LockType, deadline: float | None = None) -> Iterator[None]:
    acquired = (
        lock.acquire()
        if deadline is None
        else lock.acquire(timeout=min(TIMEOUT_MAX, max(0.0, deadline - monotonic())))
    )
    if not acquired:
        raise TimeoutError(
            "Shutdown timed out waiting for access; resources remain owned. Retry cleanup."
        )
    try:
        yield
    finally:
        lock.release()


Operation = Literal["get_temperature", "get_setpoint", "set_setpoint", "get_status"]


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

    U establishes remote communication and can change AUTO START to RUN. Every
    operation starts with U, including after idle expiry; no hidden worker runs.
    No identity query exists in the documented subset: selection is human-owned.
    """

    def __init__(self, allow_writes: bool = False) -> None:
        self._allow_writes = allow_writes
        if type(self._allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")

    @staticmethod
    def is_complete(response: bytes) -> bool:
        if response[:1] != b"#" or any(not 32 <= byte <= 126 for byte in response.rstrip(b"\r")):
            raise ProtocolError("Unexpected T255P response framing")
        return response.endswith(b"\r")

    @staticmethod
    def reply(command: bytes, response: bytes) -> bytes:
        payload = _t255p_payload(response, b"#")
        if len(payload) < 2 or payload[:1] != command:
            raise ProtocolError("T255P command echo mismatch")
        error = payload[1:2]
        if error != b"0":
            message = {b"1": "checksum", b"2": "bad command", b"3": "qualifier out of bounds"}
            raise ProtocolError(f"T255P communication error: {message.get(error, 'unknown code')}")
        return payload[2:]

    @staticmethod
    def _signed(data: bytes, digits: int) -> float:
        if len(data) != digits + 1 or data[:1] not in (b"+", b"-") or not data[1:].isdigit():
            raise ProtocolError("Malformed T255P signed temperature")
        return int(data) / (100 if digits == 4 else 10)

    @staticmethod
    def _watchdog(data: bytes) -> str:
        if len(data) != 4 or data[:1] not in b"0123" or any(c not in b"01" for c in data[1:]):
            raise ProtocolError("Malformed T255P watchdog state")
        return data.decode("ascii")

    @staticmethod
    def _alarms(data: bytes) -> str:
        if len(data) != 6 or any(c not in b"01" for c in data):
            raise ProtocolError("Malformed T255P alarm flags")
        return data.decode("ascii")

    def _memory(self, data: bytes) -> float:
        if len(data) != 9 or data[:1] != b"0" or data[5:6] != b"," or not data[6:].isdigit():
            raise ProtocolError("Malformed T255P memory response")
        target = self._signed(data[1:5], 3)
        if not -5 <= target <= 45:
            raise ProtocolError("T255P reported target outside documented -5..45 C range")
        return target

    def verify(self, io: AsciiIO) -> str:
        return self._watchdog(io(b"U", b""))

    def execute(self, operation: Operation, value: float | None, io: AsciiIO) -> float | str | None:
        target = b""
        if operation == "set_setpoint":
            if not self._allow_writes:
                raise ProtocolError(
                    "T255P writes disabled; enable with T255P(allow_writes=True) after checking setup"
                )
            number = _finite(value, "Setpoint")
            if not 5 <= number <= 45:
                raise ProtocolError("T255P water protocol supports targets from 5 to 45 C only")
            if abs(number * 10 - round(number * 10)) > 1e-8:
                raise ProtocolError("T255P targets require 0.1 C resolution; no rounding")
            target = f"{round(number * 10):+04d}".encode("ascii")
        state = self.verify(io)
        if operation == "get_setpoint":
            return self._memory(io(b"H", b"0"))
        alarms = self._alarms(io(b"J", b""))
        if operation == "get_status":
            mode = ("AUTO START", "STANDBY", "RUN", "SAFETY DEFAULT")[int(state[0])]
            names = ("float_switch", "high_temp", "low_temp", "sensor", "eeprom", "watchdog")
            flags = " ".join(f"{name}={flag}" for name, flag in zip(names, alarms, strict=True))
            return (
                f"T255P (human-selected; identity not queried); mode={mode}; "
                f"summary_alarm={state[1]} chiller_on={state[2]} dryer_on={state[3]}; "
                f"reported alarms: {flags}; flow/leak unknown; last serial reply, not safety proof"
            )
        if operation == "get_temperature":
            if alarms[3] == "1":
                raise ProtocolError("T255P sensor alarm; temperature unavailable")
            return self._signed(io(b"I", b""), 4)
        if state[0] != "2" or state[1] != "0" or "1" in alarms:
            raise ProtocolError("T255P must report RUN with no active alarms before a target write")
        if io(b"M", target) != target:
            raise ProtocolError("T255P target echo mismatch; outcome uncertain")
        if self._memory(io(b"H", b"0")) != int(target) / 10:
            raise ProtocolError("T255P target readback mismatch; outcome uncertain")
        return None


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

    def execute(
        self, operation: Operation, value: float | None, io: RegisterIO
    ) -> float | str | None:
        if operation == "get_temperature":
            self._units(io)
            display = io(3, 0x0306, 1)
            if display & 0x20:
                raise ProtocolError(
                    f"Controller reports FAIL (display=0x{display:02x}); reading unavailable"
                )
            return self._temperature(io(3, 0x001C, 1))
        if operation == "get_setpoint":
            self._units(io)
            return self._temperature(io(3, 0x007F, 1))
        if operation == "get_status":
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
        return None


def _io_error(error: Exception) -> OSError:
    if isinstance(error, TimeoutError):
        return TimeoutError(str(error))
    try:
        from serial import SerialTimeoutException
    except ImportError:
        pass
    else:
        if isinstance(error, SerialTimeoutException):
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
        self._lock = Lock()
        self._intent_lock = Lock()
        self._cancel = Event()
        self._cancel.set()
        self._fault = ""
        self._backend_name = "unknown"
        self._monitor_lock = Lock()
        self._data_lock = Lock()
        self._interval_s = 1.0
        self._history: deque[dict[str, Any]] = deque(maxlen=3600)
        self._stop = Event()
        self._thread: Thread | None = None
        self._service_error = ""
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
            config.update(port=None, timeout=self.timeout_s, write_timeout=self.timeout_s)
            if self._serial_factory is None:
                try:
                    from serial import Serial
                except ImportError as exc:
                    raise OSError(
                        "pyserial is required for hardware communication; reinstall tark-mrc-chiller "
                        "with its dependencies or run: python -m pip install pyserial"
                    ) from exc
                self._serial = Serial(**config)
            else:
                self._serial = self._serial_factory(**config)
            if self._serial.is_open:
                raise OSError("Serial factory must return an unopened endpoint")
            self._serial.port = self._settings["port"]
            self._serial.rts = rts
            self._serial.dtr = dtr
            if self._rs485 is not None:
                from serial.rs485 import RS485Settings

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
            self._failed(exc)
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

    def _failed(self, error: BaseException) -> None:
        try:
            self._close_port()
        except BaseException as cleanup:
            error.add_note(f"Serial cleanup failed: {type(cleanup).__name__}: {cleanup}")

    def _transfer(self, request: bytes, is_complete: Callable[[bytes], bool]) -> bytes:
        deadline = perf_counter() + self.timeout_s

        def remaining() -> float:
            budget = deadline - perf_counter()
            if budget <= 0:
                raise TimeoutError("Serial transaction timed out; outcome may be unknown")
            return budget

        if not isinstance(request, bytes) or not request:
            raise ProtocolError("Protocol must supply a nonempty byte request")
        self._serial.write_timeout = remaining()
        written = self._serial.write(request)
        if type(written) is not int or written != len(request):
            raise OSError("Partial serial write; outcome unknown, not retried")
        response = bytearray()
        while True:
            self._serial.timeout = remaining()
            chunk = self._serial.read(1)
            remaining()
            if not isinstance(chunk, bytes) or len(chunk) > 1:
                raise OSError("Serial read violated requested byte count")
            response.extend(chunk)
            if chunk:
                complete = is_complete(bytes(response))
                remaining()
                if complete:
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
            sleep(delay)
        self._serial.reset_input_buffer()
        request = _t255p_frame(b"." + command + data)
        self._t255p_next_command_at = perf_counter() + 3.0
        if command == b"M":
            self._write_uncertain = True
        response = self._transfer(request, self._protocol.is_complete)
        result = self._protocol.reply(command, response)
        if self._serial.in_waiting:
            raise ProtocolError("Unexpected trailing T255P response bytes")
        self._t255p_next_command_at = 0.0
        return result

    def _exchange(
        self, operation: Operation, value: float | None = None
    ) -> float | dict[str, Any] | None:
        if not self._port_connected:
            raise ConnectionError("Serial device is disconnected")
        assert self._protocol is not None
        try:
            if isinstance(self._protocol, Cal33xx):
                result = self._protocol.execute(operation, value, self._cal_io)
                if operation == "set_setpoint":
                    self._write_uncertain = False
            elif isinstance(self._protocol, T255P):
                result = self._protocol.execute(operation, value, self._t255p_io)
                if operation == "set_setpoint":
                    self._write_uncertain = False
            if operation in ("get_temperature", "get_setpoint"):
                return _reading(result)
            if operation == "get_status":
                if not isinstance(result, str):
                    raise ProtocolError("Protocol returned invalid hardware status")
                simulated = getattr(self._serial, "_simulated", False) is True
                return {
                    "connected": True,
                    "backend": "simulator" if simulated else "hardware",
                    "detail": ("Simulation; " if simulated else "") + result,
                }
            if result is not None:
                raise ProtocolError("Protocol did not acknowledge the setpoint write")
            return None
        except BaseException as exc:
            self._failed(exc)
            if self._write_uncertain and isinstance(exc, Exception):
                device = "T255P" if isinstance(self._protocol, T255P) else "CAL"
                raise ProtocolError(
                    f"{device} write outcome uncertain; target/control state may have changed. "
                    "No retry or automatic recovery write. Human inspection required. " + str(exc)
                ) from exc
            if not isinstance(exc, Exception) or isinstance(exc, ProtocolError):
                raise
            raise _io_error(exc) from exc

    def _read_temperature(self) -> float:
        return cast(float, self._exchange("get_temperature"))

    def _read_setpoint(self) -> float:
        return cast(float, self._exchange("get_setpoint"))

    def _write_setpoint(self, value: float) -> None:
        self._exchange("set_setpoint", value)

    def _read_status(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._exchange("get_status"))

    @property
    def setpoint_range(self) -> tuple[float, float]:
        return self._setpoint_range

    @property
    def coolant(self) -> str:
        return self._coolant

    @property
    def coolant_source(self) -> str:
        return self._coolant_source

    def _intent(self, connected: bool, *, deadline: float | None = None) -> Event:
        """Cancel older requests before waiting for device I/O."""
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
        token = self._intent(connected=True)
        with self._lock:
            self._check_cancel(token)
            self._fault = ""
            self._read(lambda: self._open(token), token, connecting=True)

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
        token = self._intent(connected=False, deadline=deadline)
        with _locked_until(self._monitor_lock, deadline):
            self._stop.set()
            try:
                with _locked_until(self._lock, deadline):
                    if token is self._cancel:
                        self._close_port()
            except BaseException as error:
                try:
                    self._join(max(0.0, deadline - monotonic()))
                except BaseException as cleanup:
                    error.add_note(f"Monitor cleanup failed: {type(cleanup).__name__}: {cleanup}")
                raise
            self._join(max(0.0, deadline - monotonic()))

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return not self._cancel.is_set() and not self._fault and self._port_connected

    def _suspend(self, message: str, error: BaseException) -> None:
        self._fault = message
        self._failed(error)

    def _read[T](self, operation: Callable[[], T], token: Event, *, connecting: bool = False) -> T:
        """Recover reads and connection attempts; writes never enter this loop."""
        for attempt in range(self._reconnect_attempts + 1):
            try:
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
                    self._require_connected()
                result = operation()
                self._check_cancel(token)
                return result
            except ProtocolError as exc:
                self._suspend(f"Protocol failure; recovery suspended: {exc}", exc)
                raise
            except OSError as exc:
                self._check_cancel(token)
                if self._fault or self._reconnect_attempts == 0:
                    raise
                if attempt == self._reconnect_attempts:
                    self._suspend(f"Recovery exhausted: {exc}; call connect() explicitly", exc)
                    raise ConnectionError(self._fault) from exc
        raise AssertionError("Unreachable recovery state")

    def read_temperature(self) -> float:
        with self._lock:
            return self._read(lambda: _reading(self._read_temperature()), self._cancel)

    def read_setpoint(self) -> float:
        with self._lock:
            return self._read(lambda: _reading(self._read_setpoint()), self._cancel)

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
                self._write_setpoint(value)
            except ProtocolError as exc:
                self._suspend(f"Protocol failure; recovery suspended: {exc}", exc)
                raise

    def read_status(self) -> dict[str, Any]:
        with self._lock:
            if self._cancel.is_set() or self._fault:
                return {
                    "connected": False,
                    "backend": self._backend_name,
                    "detail": self._fault or "disconnected",
                }

            def read() -> dict[str, Any]:
                status = self._read_status()
                if not isinstance(status, dict) or (
                    type(status.get("connected")) is not bool
                    or not isinstance(status.get("backend"), str)
                    or (not status["backend"].strip())
                    or (not isinstance(status.get("detail"), str))
                ):
                    raise ProtocolError("Controller returned an invalid status")
                self._backend_name = status["backend"]
                return {
                    "connected": status["connected"],
                    "backend": status["backend"],
                    "detail": status["detail"],
                }

            return self._read(read, self._cancel)

    def start_monitoring(
        self,
        *,
        interval_s: float = 1.0,
        csv_path: str | Path | None = None,
        history_size: int = 3600,
    ) -> None:
        """Start one worker; read its history and errors through snapshot()."""
        interval = _seconds(interval_s, "Sampling interval")
        if type(history_size) is not int or history_size <= 0:
            raise ValueError("history_size must be a positive integer")
        with self._monitor_lock:
            with self._lock:
                self._require_connected()
            if self.running:
                raise RuntimeError("Monitoring already started; stop it before another run")
            self._close_csv()
            if self._file is not None:
                raise OSError(self._logging_error)
            stop = Event()
            stream = None
            if csv_path is not None:
                path = Path(csv_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = path.open("x", newline="", encoding="utf-8")
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
                    self._file = stream
                    try:
                        self._close_csv()
                    except BaseException as cleanup:
                        error.add_note(f"CSV cleanup failed: {type(cleanup).__name__}: {cleanup}")
                    if self._file is not None:
                        error.add_note(self._logging_error)
                    raise
            with self._data_lock:
                self._file = stream
                self._interval_s = interval
                self._history = deque(maxlen=history_size)
                self._stop = stop
                self._thread = None
                self._service_error = self._logging_error = ""
                self._logged_samples = self._sample_count = self._failed_samples = 0
            self._start()

    def stop_monitoring(self, timeout_s: float = 5.0) -> None:
        """Bound lock wait plus worker join; keep the connection for synchronous use."""
        deadline = monotonic() + _seconds(timeout_s, "Stop timeout")
        with _locked_until(self._monitor_lock, deadline):
            self._join(max(0.0, deadline - monotonic()))

    def __enter__(self) -> "TarkChiller":
        try:
            self.connect()
        except BaseException as error:
            self.__exit__(None, error, None)
            raise
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
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict[str, Any]:
        with self._data_lock:
            return {
                "history": [
                    {**sample, "status": sample["status"].copy()} for sample in self._history
                ],
                "running": self.running,
                "service_error": self._service_error,
                "logging_error": self._logging_error,
                "logging_enabled": self._file is not None,
                "logged_samples": self._logged_samples,
                "sample_count": self._sample_count,
                "failed_samples": self._failed_samples,
            }

    def _close_csv(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except BaseException as exc:
                with self._data_lock:
                    detail = f"CSV close failed: {type(exc).__name__}: {exc}"
                    if detail not in self._logging_error:
                        self._logging_error = "\n".join(filter(None, (self._logging_error, detail)))
                if not isinstance(exc, OSError):
                    raise
            else:
                with self._data_lock:
                    self._file = None

    def _start(self) -> None:
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
                    error.add_note(f"CSV cleanup failed: {type(cleanup).__name__}: {cleanup}")
                if self._file is not None:
                    error.add_note(self._logging_error)
            raise
        finally:
            if ready is not None:
                ready.set()

    def _join(self, timeout_s: float) -> None:
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
        if self._file is not None:
            raise OSError(self._logging_error)

    def _poll(self, started: float) -> dict[str, Any]:
        sampled_monotonic = time.monotonic()
        timestamp_utc = datetime.now(UTC)
        status = {"connected": False, "backend": "unknown", "detail": "unavailable"}
        error = ""
        try:
            status = self.read_status()
            temperature = self.read_temperature()
            setpoint = self.read_setpoint()
        except (OSError, ProtocolError) as exc:
            temperature = None
            setpoint = None
            status = {"connected": False, "backend": status["backend"], "detail": "poll failed"}
            error = str(exc) or type(exc).__name__
        return {
            "timestamp_utc": timestamp_utc,
            "elapsed_s": sampled_monotonic - started,
            "temperature_c": temperature,
            "setpoint_c": setpoint,
            "status": status,
            "error": error,
            "sampled_monotonic": sampled_monotonic,
        }

    def _publish(self, sample: dict[str, Any]) -> None:
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
                self._close_csv()
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
                self._publish(self._poll(started))
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
                    self._logging_error = f"CSV close failed: {type(exc).__name__}: {exc}"
