"""Hardware-independent core API; Dash and pySerial are optional dependencies."""

from .api import Chiller
from .device import ChillerDevice, DeviceStatus
from .errors import (
    ChillerConnectionError,
    ChillerError,
    ProtocolError,
    ProtocolUnavailableError,
    SetpointValidationError,
    TransportError,
)
from .safety import DISTILLED_WATER, CoolantProfile
from .simulator import SimulatedDevice

__all__ = [
    "Chiller",
    "ChillerConnectionError",
    "ChillerDevice",
    "ChillerError",
    "CoolantProfile",
    "DISTILLED_WATER",
    "DeviceStatus",
    "ProtocolError",
    "ProtocolUnavailableError",
    "SetpointValidationError",
    "SimulatedDevice",
    "TransportError",
]
