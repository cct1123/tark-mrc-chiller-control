"""Bounded generic serial I/O, with no device settings or protocol defaults.

RS485 uses pySerial's native mode; its platform/adapter support must be verified.
Opening a real port may affect RTS/DTR even with explicit requested levels.
Timeouts bound cooperating serial reads/writes, not arbitrary OS open/close calls.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from math import isfinite
from numbers import Real
from threading import TIMEOUT_MAX, RLock
from time import monotonic
from typing import Any, Protocol

from .errors import ProtocolError, TransportError


def _finite_number(value: object, name: str, *, positive: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be a finite real number")
    if number < 0 or (positive and number == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")


@dataclass(frozen=True)
class SerialSettings:
    """All electrical/framing settings are explicit, not inferred for MRC units."""

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
        if not isinstance(self.parity, str) or self.parity not in ("N", "E", "O", "M", "S"):
            raise ValueError("unsupported serial parity")
        if isinstance(self.stopbits, bool) or self.stopbits not in (1, 1.5, 2):
            raise ValueError("stopbits must be 1, 1.5, or 2")
        for name in ("xonxoff", "rtscts", "dsrdtr", "rts", "dtr"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be an explicit boolean")


@dataclass(frozen=True)
class RS485Mode:
    """Explicit pySerial native direction-control settings, not Tark defaults."""

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
            value = getattr(self, name)
            if value is not None:
                _finite_number(value, name, positive=False)


class Transport(Protocol):
    @property
    def is_open(self) -> bool: ...

    def open(self) -> None: ...

    def close(self) -> None: ...

    def exchange(self, request: bytes, is_complete: Callable[[bytes], bool]) -> bytes: ...


def _serial_factory(**settings: Any) -> Any:
    try:
        from serial import Serial
    except ImportError as exc:
        raise TransportError("Install the serial extra to use a physical transport") from exc
    return Serial(**settings)


def _rs485_mode_factory(**settings: Any) -> Any:
    from serial.rs485 import RS485Settings

    return RS485Settings(**settings)


class RS232Transport:
    """One outstanding transaction, one write attempt, bounded response memory.

    The 1 second and 4096 byte defaults are software budgets, not device facts.
    Injected serial factories must honor port=None and finite I/O timeouts.
    """

    def __init__(
        self,
        settings: SerialSettings,
        *,
        transaction_timeout_s: float = 1.0,
        max_response_bytes: int = 4096,
        serial_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isinstance(settings, SerialSettings):
            raise ValueError("settings must be explicit SerialSettings")
        _finite_number(transaction_timeout_s, "transaction_timeout_s", positive=True)
        if transaction_timeout_s > TIMEOUT_MAX:
            raise ValueError("transaction_timeout_s exceeds the platform lock timeout limit")
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be a positive integer")
        self.settings = settings
        self.transaction_timeout_s = float(transaction_timeout_s)
        self.max_response_bytes = max_response_bytes
        self._factory = serial_factory or _serial_factory
        self._clock = clock
        self._serial: Any = None
        self._usable = False
        self._lock = RLock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._usable and self._serial is not None and bool(self._serial.is_open)

    def _configure_mode(self, serial: Any) -> None:
        pass

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return
            self.close()
            try:
                config = asdict(self.settings)
                config.update(
                    port=None,
                    timeout=self.transaction_timeout_s,
                    write_timeout=self.transaction_timeout_s,
                )
                rts, dtr = config.pop("rts"), config.pop("dtr")
                self._serial = self._factory(**config)
                if self._serial.is_open:
                    raise TransportError("Serial factory must return an unopened port")
                self._serial.port = self.settings.port
                self._serial.rts, self._serial.dtr = rts, dtr
                self._configure_mode(self._serial)
                self._serial.open()
                if not self._serial.is_open:
                    raise TransportError("Serial port did not open")
                self._usable = True
            except Exception as exc:
                self._cleanup_after_error(exc)
                if isinstance(exc, TransportError):
                    raise
                raise TransportError(f"Serial open failed: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._usable = False
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception as exc:
                    # Keep the handle for an explicit later cleanup attempt.
                    raise TransportError(f"Serial close failed: {exc}") from exc
                self._serial = None

    def _cleanup_after_error(self, original: Exception) -> None:
        try:
            self.close()
        except TransportError as cleanup_error:
            original.add_note(str(cleanup_error))

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise TransportError("Serial transaction timed out; outcome may be unknown")
        return remaining

    def exchange(self, request: bytes, is_complete: Callable[[bytes], bool]) -> bytes:
        if not isinstance(request, bytes) or not request:
            raise ProtocolError("Codec must provide a nonempty byte request")
        deadline = self._clock() + self.transaction_timeout_s
        if not self._lock.acquire(timeout=self.transaction_timeout_s):
            raise TransportError("Timed out waiting for the serial transaction lock")
        try:
            if not self.is_open:
                raise TransportError("Serial transport is closed")
            try:
                self._serial.write_timeout = self._remaining(deadline)
                written = self._serial.write(request)
                if type(written) is not int or written != len(request):
                    raise TransportError("Partial serial write; outcome unknown, not retried")
                response = bytearray()
                while True:
                    self._serial.timeout = self._remaining(deadline)
                    chunk = self._serial.read(1)
                    self._remaining(deadline)
                    if not isinstance(chunk, bytes) or len(chunk) > 1:
                        raise TransportError("Serial read violated the requested byte count")
                    response.extend(chunk)
                    if chunk and is_complete(bytes(response)):
                        return bytes(response)
                    if len(response) >= self.max_response_bytes:
                        raise ProtocolError("Response exceeded the configured byte budget")
            except Exception as exc:
                self._cleanup_after_error(exc)
                if isinstance(exc, (ProtocolError, TransportError)):
                    raise
                raise TransportError(f"Serial transaction failed: {exc}") from exc
        finally:
            self._lock.release()


class RS485Transport(RS232Transport):
    """Native RS485 mode; no software RTS timing emulation or device assumptions."""

    def __init__(
        self,
        settings: SerialSettings,
        mode: RS485Mode,
        *,
        rs485_settings_factory: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(mode, RS485Mode):
            raise ValueError("mode must be explicit RS485Mode")
        super().__init__(settings, **kwargs)
        self.mode = mode
        self._mode_factory = rs485_settings_factory or _rs485_mode_factory

    def _configure_mode(self, serial: Any) -> None:
        serial.rs485_mode = self._mode_factory(**asdict(self.mode))
