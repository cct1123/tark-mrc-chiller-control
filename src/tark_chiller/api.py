"""Small serialized public API shared by every device backend."""

import threading

from .device import ChillerDevice, DeviceStatus
from .errors import ChillerConnectionError, ProtocolError, SetpointValidationError
from .safety import DISTILLED_WATER, CoolantProfile, _finite_real


class Chiller:
    """Own application access to a backend with an immutable coolant policy.

    Connection and retry decisions are explicit. Connecting never sets a target;
    disconnecting software does not stop a physical chiller.
    """

    def __init__(self, device: ChillerDevice, coolant: CoolantProfile = DISTILLED_WATER):
        if not isinstance(coolant, CoolantProfile):
            raise SetpointValidationError("coolant must be an explicit CoolantProfile")
        self._device = device
        self._coolant = coolant
        self._lock = threading.RLock()

    @property
    def coolant(self) -> CoolantProfile:
        return self._coolant

    def connect(self) -> None:
        with self._lock:
            self._device.connect()

    def disconnect(self) -> None:
        with self._lock:
            self._device.disconnect()

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._device.is_connected

    def _require_connected(self) -> None:
        if not self._device.is_connected:
            raise ChillerConnectionError("Chiller is disconnected; call connect() explicitly")

    @staticmethod
    def _reading(value: object, label: str) -> float:
        try:
            return _finite_real(value, label)
        except SetpointValidationError as exc:
            raise ProtocolError(f"Backend returned invalid {label}") from exc

    def get_temperature(self) -> float:
        with self._lock:
            self._require_connected()
            return self._reading(self._device.get_temperature(), "temperature (°C)")

    def get_setpoint(self) -> float:
        with self._lock:
            self._require_connected()
            return self._reading(self._device.get_setpoint(), "setpoint (°C)")

    def set_setpoint(self, value_c: float) -> None:
        # Validate before accessing even the backend's connection property.
        validated = self._coolant.validate(value_c)
        with self._lock:
            self._require_connected()
            self._device.set_setpoint(validated)

    def get_status(self) -> DeviceStatus:
        """Connection status remains available while disconnected."""
        with self._lock:
            return self._device.get_status()
