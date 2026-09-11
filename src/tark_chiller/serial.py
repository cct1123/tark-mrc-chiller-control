"""Explicit serial configuration and one protocol-driven device, owned by Chiller.

No MRC commands or serial defaults are known. A codec is required before any
endpoint is created. Native RS485 support depends on the actual adapter/platform.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from math import isfinite
from threading import TIMEOUT_MAX
from time import perf_counter
from typing import Any, Literal, Protocol, cast

from .controller import Status
from .errors import ProtocolError, ProtocolUnavailableError

Operation = Literal["get_temperature", "get_setpoint", "set_setpoint", "get_status"]


def _seconds(value: object, name: str, *, allow_zero: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (not allow_zero and value == 0)
        or value > TIMEOUT_MAX
        or not isfinite(value)
    ):
        raise ValueError(
            f"{name} must be finite bounded {'nonnegative' if allow_zero else 'positive'} seconds"
        )
    return float(value)


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
            if (value := getattr(self, name)) is not None:
                _seconds(value, name, allow_zero=True)


class Codec(Protocol):
    """Documented wire format: verify identity, integrity, units and acknowledgements."""

    def encode(self, operation: Operation, value: float | None = None) -> bytes: ...

    def is_complete(self, response: bytes) -> bool: ...

    def decode(self, operation: Operation, response: bytes) -> float | Status | None: ...


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
        codec: Codec | None = None,
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
        self._last_error = "disconnected"

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
        if self._codec is None:
            self._last_error = "Controller communication manual is required; no codec is configured"
            raise ProtocolUnavailableError(self._last_error)
        if self.is_connected:
            return
        self.disconnect()
        try:
            config = asdict(self.settings)
            rts, dtr = config.pop("rts"), config.pop("dtr")
            config.update(port=None, timeout=self.timeout_s, write_timeout=self.timeout_s)
            self._endpoint = self._factory(**config)
            if self._endpoint.is_open:
                raise OSError("Serial factory must return an unopened endpoint")
            self._endpoint.port = self.settings.port
            self._endpoint.rts, self._endpoint.dtr = rts, dtr
            if self.rs485 is not None:
                from serial.rs485 import RS485Settings

                self._endpoint.rs485_mode = RS485Settings(**asdict(self.rs485))
            self._endpoint.open()
            if not self._endpoint.is_open:
                raise OSError("Serial endpoint did not open")
            self._usable = True
            self._last_error = ""
        except BaseException as exc:
            self._failed(exc)
            if not isinstance(exc, Exception):
                raise
            raise _io_error(exc) from exc

    def disconnect(self) -> None:
        self._usable = False
        if self._endpoint is not None:
            try:
                self._endpoint.close()
            except BaseException as exc:
                self._last_error = str(exc) or type(exc).__name__
                if not isinstance(exc, Exception):
                    raise
                raise _io_error(exc) from exc
            self._endpoint = None

    def _failed(self, error: BaseException) -> None:
        try:
            self.disconnect()
        except Exception as cleanup:
            error.add_note(f"Serial cleanup failed: {cleanup}")
        finally:
            self._last_error = str(error) or type(error).__name__

    def _exchange(self, operation: Operation, value: float | None = None) -> float | Status | None:
        if not self.is_connected:
            raise ConnectionError("Serial device is disconnected")
        assert self._codec is not None
        deadline = perf_counter() + self.timeout_s

        def remaining() -> float:
            budget = deadline - perf_counter()
            if budget <= 0:
                raise TimeoutError("Serial transaction timed out; outcome may be unknown")
            return budget

        try:
            try:
                request = self._codec.encode(operation, value)
            except Exception as exc:
                raise ProtocolError(f"Codec encoding failed: {exc}") from exc
            if not isinstance(request, bytes) or not request:
                raise ProtocolError("Codec must supply a nonempty byte request")
            self._endpoint.write_timeout = remaining()
            if type(written := self._endpoint.write(request)) is not int or written != len(request):
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
                        complete = self._codec.is_complete(bytes(response))
                    except Exception as exc:
                        raise ProtocolError(f"Codec framing failed: {exc}") from exc
                    if type(complete) is not bool:
                        raise ProtocolError("Codec framing must return a boolean")
                    remaining()
                    if complete:
                        break
                if len(response) >= self.max_response_bytes:
                    raise ProtocolError("Response exceeded byte budget")
            try:
                result = self._codec.decode(operation, bytes(response))
            except Exception as exc:
                raise ProtocolError(f"Codec decoding failed: {exc}") from exc
            remaining()
            if operation in ("get_temperature", "get_setpoint"):
                if isinstance(result, bool) or not isinstance(result, (int, float)):
                    raise ProtocolError("Codec returned a nonnumeric Celsius reading")
                try:
                    result = float(result)
                except OverflowError as exc:
                    raise ProtocolError("Codec returned an invalid Celsius reading") from exc
                if not isfinite(result):
                    raise ProtocolError("Codec returned a nonfinite Celsius reading")
            elif operation == "get_status":
                if (
                    not isinstance(result, Status)
                    or result.connected is not True
                    or result.backend != "hardware"
                    or not isinstance(result.detail, str)
                ):
                    raise ProtocolError("Codec returned invalid hardware status")
            elif result is not None:
                raise ProtocolError("Codec did not acknowledge the setpoint write")
            self._last_error = ""
            return result
        except BaseException as exc:
            self._failed(exc)
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
        if not self.is_connected:
            return Status(False, "hardware", self._last_error)
        return cast(Status, self._exchange("get_status"))
