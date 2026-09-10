"""The shared, synchronous backend contract; all temperatures use Celsius."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DeviceStatus:
    """Software communication status, without unverified physical safety signals."""

    connected: bool
    backend: str
    detail: str = ""


@runtime_checkable
class ChillerDevice(Protocol):
    """Common backend operations implemented by simulation and serial devices."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...

    def get_temperature(self) -> float: ...

    def get_setpoint(self) -> float: ...

    def set_setpoint(self, value_c: float) -> None: ...

    def get_status(self) -> DeviceStatus: ...
