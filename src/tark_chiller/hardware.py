"""Physical backend shell; disabled until a documented codec is supplied."""

from threading import RLock
from typing import Literal, overload

from .device import DeviceStatus
from .errors import ChillerConnectionError, ChillerError, ProtocolError, SetpointValidationError
from .protocol import MissingProtocol, Operation, ProtocolCodec
from .safety import DISTILLED_WATER, CoolantProfile, _finite_real
from .transport import Transport


class SerialDevice:
    def __init__(
        self,
        transport: Transport,
        codec: ProtocolCodec | None = None,
        coolant: CoolantProfile = DISTILLED_WATER,
    ) -> None:
        if not isinstance(coolant, CoolantProfile):
            raise SetpointValidationError("coolant must be an explicit CoolantProfile")
        self._transport = transport
        self._codec = codec if codec is not None else MissingProtocol()
        self._coolant = coolant
        self._lock = RLock()
        self._last_error = ""

    @property
    def coolant(self) -> CoolantProfile:
        return self._coolant

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._transport.is_open

    def connect(self) -> None:
        with self._lock:
            try:
                self._codec.ensure_available()  # Must precede even port opening.
                self._transport.open()
                self._last_error = ""
            except ChillerError as exc:
                self._last_error = str(exc)
                raise

    def disconnect(self) -> None:
        with self._lock:
            self._transport.close()

    @overload
    def _call(
        self, operation: Literal["get_temperature", "get_setpoint"], value: None = None
    ) -> float: ...

    @overload
    def _call(self, operation: Literal["get_status"], value: None = None) -> DeviceStatus: ...

    @overload
    def _call(self, operation: Literal["set_setpoint"], value: float) -> None: ...

    def _call(
        self, operation: Operation, value: float | None = None
    ) -> float | DeviceStatus | None:
        with self._lock:
            if not self.is_connected:
                raise ChillerConnectionError("Chiller is disconnected")
            try:
                self._codec.ensure_available()
                request = self._codec.encode(operation, value)
                response = self._transport.exchange(request, self._codec.is_complete)
                result = self._codec.decode(operation, response)
                if operation in ("get_temperature", "get_setpoint"):
                    try:
                        result = _finite_real(result, "decoded Celsius value")
                    except SetpointValidationError as exc:
                        raise ProtocolError(
                            "Codec returned a non-finite or nonnumeric Celsius value"
                        ) from exc
                elif operation == "get_status":
                    if not isinstance(result, DeviceStatus):
                        raise ProtocolError("Codec did not return DeviceStatus")
                    if result.backend != "hardware" or result.connected is not True:
                        raise ProtocolError("Codec returned inconsistent connection/backend status")
                elif result is not None:
                    raise ProtocolError("Codec did not normalize setpoint success to None")
                self._last_error = ""
                return result
            except Exception as exc:
                self._last_error = str(exc)
                try:
                    self._transport.close()
                except ChillerError as cleanup_error:
                    exc.add_note(str(cleanup_error))
                if isinstance(exc, ChillerError):
                    raise
                raise ProtocolError(f"Controller protocol failed: {exc}") from exc

    def get_temperature(self) -> float:
        return self._call("get_temperature")

    def get_setpoint(self) -> float:
        return self._call("get_setpoint")

    def set_setpoint(self, value_c: float) -> None:
        value = self.coolant.validate(value_c)
        self._call("set_setpoint", value)

    def get_status(self) -> DeviceStatus:
        with self._lock:
            if not self.is_connected:
                return DeviceStatus(False, "hardware", self._last_error or "disconnected")
            return self._call("get_status")
