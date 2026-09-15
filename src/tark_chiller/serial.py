"""Explicit serial configuration and one protocol-driven device, owned by Chiller.

CAL 3300/9300 commands follow the manufacturer's guide; see docs/protocol.md.
Actual controller, wiring and settings require confirmation before physical use.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import monotonic, perf_counter, sleep
from typing import Any, Literal, Protocol, cast

from .controller import ProtocolError, Simulator, Status, _finite, _reading, _seconds

Operation = Literal["get_temperature", "get_setpoint", "set_setpoint", "get_status"]


@dataclass(frozen=True)
class SerialSettings:
    """Values must come from the matching controller and adapter documentation."""

    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: float
    xonxoff: bool
    rtscts: bool
    dsrdtr: bool
    rts: bool
    dtr: bool

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("port must be explicitly named")
        if type(self.baudrate) is not int or self.baudrate <= 0:
            raise ValueError("baudrate must be a positive integer")
        if type(self.bytesize) is not int or self.bytesize not in (5, 6, 7, 8):
            raise ValueError("bytesize must be 5, 6, 7, or 8")
        if self.parity not in ("N", "E", "O", "M", "S"):
            raise ValueError("unsupported parity")
        if isinstance(self.stopbits, bool) or self.stopbits not in (1, 1.5, 2):
            raise ValueError("stopbits must be 1, 1.5, or 2")
        for name in ("xonxoff", "rtscts", "dsrdtr", "rts", "dtr"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be an explicit boolean")


@dataclass(frozen=True)
class RS485Mode:
    rts_level_for_tx: bool
    rts_level_for_rx: bool
    loopback: bool
    delay_before_tx: float | None
    delay_before_rx: float | None

    def __post_init__(self) -> None:
        for name in ("rts_level_for_tx", "rts_level_for_rx", "loopback"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be an explicit boolean")
        for name in ("delay_before_tx", "delay_before_rx"):
            delay = getattr(self, name)
            if delay is not None:
                _seconds(delay, name, allow_zero=True)


class Codec(Protocol):
    """Documented wire format: verify identity, integrity, units and acknowledgements."""

    def encode(self, operation: Operation, value: float | None = None) -> bytes: ...

    def is_complete(self, response: bytes) -> bool: ...

    def decode(self, operation: Operation, response: bytes) -> float | str | None: ...


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


@dataclass(frozen=True)
class Cal33xx:
    """Documented CAL 3300/9300 subset; no automatic configuration or reset.

    Reads verify supported family/firmware, RTD input and Celsius units.
    Writes need explicit opt-in and exact expected identity codes from a prior read.
    This candidate accepts nonnegative temperatures only; negative wire encoding
    is not specified by the source. Each instance belongs to one SerialDevice.
    """

    address: int
    allow_writes: bool = False
    expected_model: int | None = None
    expected_firmware: int | None = None

    def __post_init__(self) -> None:
        if type(self.address) is not int or not 1 <= self.address <= 247:
            raise ValueError("CAL address must be 1..247; broadcasts are forbidden")
        if type(self.allow_writes) is not bool:
            raise ValueError("allow_writes must be boolean")
        for name, allowed in (("expected_model", (1, 2, 3)), ("expected_firmware", (0xFFFF, 1, 2))):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value not in allowed):
                raise ValueError(f"Unsupported CAL {name}")
        if self.allow_writes and (self.expected_model is None or self.expected_firmware is None):
            raise ValueError("Writes require expected_model and expected_firmware from read review")

    def verify(self, io: RegisterIO) -> str:
        model, firmware = io(3, 0x04FC, 1), io(3, 0x04FD, 1)
        if model not in (1, 2, 3) or firmware not in (0xFFFF, 1, 2):
            raise ProtocolError(
                f"Unsupported CAL identity: model=0x{model:04x}, firmware=0x{firmware:04x}"
            )
        if (self.expected_model is not None and model != self.expected_model) or (
            self.expected_firmware is not None and firmware != self.expected_firmware
        ):
            raise ProtocolError("CAL identity differs from the reviewed controller")
        self._units(io)
        return f"CAL 3300/9300 model=0x{model:04x} firmware=0x{firmware:04x}"

    @staticmethod
    def _units(io: RegisterIO) -> None:
        if io(3, 0x0198, 1) != 10 or io(3, 0x0199, 1) != 1:
            raise ProtocolError(
                "This CAL candidate requires Inpt=RTD and Unit=C; no settings were changed"
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
        if not self.allow_writes:
            raise ProtocolError(
                "CAL writes disabled; complete read-only review before enabling allow_writes"
            )
        target = _finite(value, "Setpoint")
        if not 0 <= target <= 40:
            raise ProtocolError("This CAL candidate supports targets from 0 to 40 C only")
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


def _serial_factory(**settings: Any) -> Any:
    try:
        from serial import Serial
    except ImportError as exc:
        raise OSError("Install the serial extra for serial communication") from exc
    return Serial(**settings)


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


class SerialDevice:
    """One endpoint; Chiller serializes access and validates all public writes.

    Private read/write hooks are backend operations, not user control methods.
    One write attempt per transaction; timeouts never cause a write replay.
    Deadlines bound cooperating I/O, not uncooperative OS open/close calls.
    """

    def __init__(
        self,
        settings: SerialSettings,
        *,
        rs485: RS485Mode | None = None,
        codec: Codec | Cal33xx | None = None,
        timeout_s: float = 1.0,
        max_response_bytes: int = 4096,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(settings, SerialSettings):
            raise ValueError("settings must be explicit SerialSettings")
        if rs485 is not None and not isinstance(rs485, RS485Mode):
            raise ValueError("rs485 must be explicit RS485Mode or None")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        self._settings = settings
        self._rs485 = rs485
        self._timeout_s = _seconds(timeout_s, "timeout_s")
        self._max_response_bytes = max_response_bytes
        self._codec = codec
        self._factory = serial_factory if serial_factory is not None else _serial_factory
        self._endpoint: Any = None
        self._usable = False
        self._write_uncertain = False

    @property
    def settings(self) -> SerialSettings:
        return self._settings

    @property
    def rs485(self) -> RS485Mode | None:
        return self._rs485

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def max_response_bytes(self) -> int:
        return self._max_response_bytes

    @property
    def is_connected(self) -> bool:
        return self._usable and self._endpoint is not None and bool(self._endpoint.is_open)

    def connect(self) -> None:
        if self._write_uncertain:
            raise ProtocolError(
                "CAL write outcome uncertain; inspect hardware before creating a new session"
            )
        if self._codec is None:
            raise ProtocolError(
                "Controller communication manual is required; no codec is configured"
            )
        if self.is_connected:
            return
        if isinstance(self._codec, Cal33xx):
            s = self.settings
            if (
                s.baudrate not in (1200, 2400, 4800, 9600, 19200)
                or s.bytesize != 8
                or s.parity not in ("N", "E", "O")
                or s.stopbits != 1
                or s.xonxoff
                or s.rtscts
                or s.dsrdtr
            ):
                raise ProtocolError("CAL needs documented baud, 8N1/8E1/8O1 and no flow control")
        self.disconnect()
        try:
            config = asdict(self.settings)
            rts = config.pop("rts")
            dtr = config.pop("dtr")
            config.update(port=None, timeout=self.timeout_s, write_timeout=self.timeout_s)
            self._endpoint = self._factory(**config)
            if self._endpoint.is_open:
                raise OSError("Serial factory must return an unopened endpoint")
            self._endpoint.port = self.settings.port
            self._endpoint.rts = rts
            self._endpoint.dtr = dtr
            if self.rs485 is not None:
                from serial.rs485 import RS485Settings

                self._endpoint.rs485_mode = RS485Settings(**asdict(self.rs485))
            self._endpoint.open()
            if not self._endpoint.is_open:
                raise OSError("Serial endpoint did not open")
            self._usable = True
            if isinstance(self._codec, Cal33xx):
                self._codec.verify(self._cal_io)
        except BaseException as exc:
            self._failed(exc)
            if not isinstance(exc, Exception) or isinstance(exc, ProtocolError):
                raise
            raise _io_error(exc) from exc

    def disconnect(self) -> None:
        self._usable = False
        if self._endpoint is not None:
            try:
                self._endpoint.close()
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    raise
                raise _io_error(exc) from exc
            self._endpoint = None

    def _failed(self, error: BaseException) -> None:
        try:
            self.disconnect()
        except Exception as cleanup:
            error.add_note(f"Serial cleanup failed: {cleanup}")

    def _transfer(self, request: bytes, is_complete: Callable[[bytes], bool]) -> bytes:
        deadline = perf_counter() + self.timeout_s

        def remaining() -> float:
            budget = deadline - perf_counter()
            if budget <= 0:
                raise TimeoutError("Serial transaction timed out; outcome may be unknown")
            return budget

        if not isinstance(request, bytes) or not request:
            raise ProtocolError("Codec must supply a nonempty byte request")
        self._endpoint.write_timeout = remaining()
        written = self._endpoint.write(request)
        if type(written) is not int or written != len(request):
            raise OSError("Partial serial write; outcome unknown, not retried")
        response = bytearray()
        while True:
            self._endpoint.timeout = remaining()
            chunk = self._endpoint.read(1)
            remaining()
            if not isinstance(chunk, bytes) or len(chunk) > 1:
                raise OSError("Serial read violated requested byte count")
            response.extend(chunk)
            if chunk:
                try:
                    complete = is_complete(bytes(response))
                except Exception as exc:
                    raise ProtocolError(f"Codec framing failed: {exc}") from exc
                if type(complete) is not bool:
                    raise ProtocolError("Codec framing must return a boolean")
                remaining()
                if complete:
                    return bytes(response)
            if len(response) >= self.max_response_bytes:
                raise ProtocolError("Response exceeded byte budget")

    def _cal_io(self, function: int, address: int, value: int) -> int:
        assert isinstance(self._codec, Cal33xx)
        # At least 3.5 characters between frames; one write() supplies each burst.
        bits = 1 + self.settings.bytesize + (self.settings.parity != "N") + self.settings.stopbits
        sleep(3.5 * bits / self.settings.baudrate)
        self._endpoint.reset_input_buffer()
        request = _rtu(
            bytes((self._codec.address, function))
            + address.to_bytes(2, "big")
            + value.to_bytes(2, "big")
        )
        if function == 6:
            self._write_uncertain = True  # Latched until the whole sequence succeeds.
        response = self._transfer(request, _rtu_complete)
        sleep(3.5 * bits / self.settings.baudrate)
        if self._endpoint.in_waiting:
            raise ProtocolError("Unexpected trailing CAL response bytes")
        if _rtu(response[:-2]) != response or response[0] != self._codec.address:
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

    def _exchange(self, operation: Operation, value: float | None = None) -> float | Status | None:
        if not self.is_connected:
            raise ConnectionError("Serial device is disconnected")
        assert self._codec is not None
        try:
            if isinstance(self._codec, Cal33xx):
                result = self._codec.execute(operation, value, self._cal_io)
                if operation == "set_setpoint":
                    self._write_uncertain = False
            else:
                started = perf_counter()
                try:
                    request = self._codec.encode(operation, value)
                except Exception as exc:
                    raise ProtocolError(f"Codec encoding failed: {exc}") from exc
                response = self._transfer(request, self._codec.is_complete)
                try:
                    result = self._codec.decode(operation, response)
                except Exception as exc:
                    raise ProtocolError(f"Codec decoding failed: {exc}") from exc
                if perf_counter() - started > self.timeout_s:
                    raise TimeoutError("Codec decoding timed out")
            if operation in ("get_temperature", "get_setpoint"):
                return _reading(result)
            if operation == "get_status":
                if not isinstance(result, str):
                    raise ProtocolError("Codec returned invalid hardware status")
                simulated = isinstance(self._endpoint, CalSimulator)
                return Status(
                    connected=True,
                    backend="simulator" if simulated else "hardware",
                    detail=("Simulation; " if simulated else "") + result,
                )
            if result is not None:
                raise ProtocolError("Codec did not acknowledge the setpoint write")
            return None
        except BaseException as exc:
            self._failed(exc)
            if self._write_uncertain and isinstance(exc, Exception):
                raise ProtocolError(
                    "CAL write outcome uncertain; keypad may be locked or settings staged/saved. "
                    "No retry or automatic unlock. Human inspection required. " + str(exc)
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

    def _read_status(self) -> Status:
        return cast(Status, self._exchange("get_status"))


class CalSimulator:
    """Memory-only CAL serial endpoint, using the same frames as the driver.

    Factory MRC target is 10 C; initial liquid is a synthetic 20 C. Staging and
    keypad locking follow CAL section 2.3. Temperature dynamics are uncalibrated.
    Closing the serial connection does not stop the simulated controller.
    registers/coils expose selected device state for deliberate fault tests.
    """

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._model = Simulator(clock=clock, initial_setpoint_c=10.0)
        self.registers = {
            0x04FC: 1,
            0x04FD: 2,
            0x0198: 10,
            0x0199: 1,
            0x0096: 0,
            0x0094: 400,
            0x0125: 2,
            0x0306: 0,
            0x0305: 0,
            0x007F: 100,
        }
        self.coils = {0x0028: 0, 0x002A: 1}
        self.is_open = False
        self.timeout = self.write_timeout = 1.0
        self.port: str | None = None
        self.rts = self.dtr = False
        self.remote_program_mode = False
        self.front_panel_busy = False
        self.applied_writes = 0
        self._security = 0
        self._buffer = b""

    def device(self) -> SerialDevice:
        """One production SerialDevice with an explicitly simulated endpoint."""
        settings = SerialSettings("MEMORY-ONLY", 9600, 8, "N", 1, False, False, False, False, False)
        return SerialDevice(settings, codec=Cal33xx(1, True, 1, 2), serial_factory=self.factory)

    def factory(self, **settings: Any) -> "CalSimulator":
        if self.is_open:
            raise OSError("Simulated endpoint already open")
        self.timeout, self.write_timeout = settings["timeout"], settings["write_timeout"]
        return self

    def open(self) -> None:
        self.is_open = True
        self._model.connect()

    def close(self) -> None:
        self.is_open = False
        self._buffer = b""

    def reset_input_buffer(self) -> None:
        self._buffer = b""

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def write(self, request: bytes) -> int:
        if not self.is_open:
            raise OSError("Simulated endpoint closed")
        self._buffer = self._respond(request)
        return len(request)

    def read(self, size: int = 1) -> bytes:
        if not self._buffer:
            sleep(self.timeout)
        result, self._buffer = self._buffer[:size], self._buffer[size:]
        return result

    def _respond(self, request: bytes) -> bytes:
        if len(request) != 8 or _rtu(request[:-2]) != request or request[0] != 1:
            return b""
        function = request[1]
        address, value = int.from_bytes(request[2:4], "big"), int.from_bytes(request[4:6], "big")
        security, self._security = self._security, 0  # Consumed by the very next message.

        def error(code: int) -> bytes:
            return _rtu(bytes((1, function | 0x80, code)))

        if function in (1, 3):
            if value != 1:
                return error(1)
            if function == 1:
                if address not in self.coils:
                    return error(2)
                return _rtu(bytes((1, 1, 1, self.coils[address])))
            if address == 0x001C:
                raw = round(self._model._read_temperature() * 10)
            elif address in self.registers:
                raw = self.registers[address]
            else:
                return error(2)
            return _rtu(b"\x01\x03\x02" + (raw & 0xFFFF).to_bytes(2, "big"))
        if function != 6:
            return b""  # Older CAL firmware does not reliably reject unknown functions.
        if address == 0x0300:
            self._security = value
        elif address == 0x1500:
            if security != 5:
                return b""
            if self.front_panel_busy:
                return error(6)
            self.remote_program_mode = True
        elif address == 0x007F:
            self.registers[address] = value  # Staged only; firmware does not validate limits.
        elif address == 0x1600:
            if security != 6:
                return b""
            if not self.remote_program_mode:
                return error(1)
            self._model._write_setpoint(self.registers[0x007F] / 10)
            self.remote_program_mode = False
            self.applied_writes += 1
        else:
            return error(2)  # Deliberately implement only the candidate's subset.
        return request
