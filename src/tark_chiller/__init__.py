"""Hardware-independent core API; Dash and pySerial are optional dependencies."""

from .api import Chiller, RecoveryPolicy
from .device import ChillerDevice, DeviceStatus
from .errors import (
    ChillerConnectionError,
    ChillerError,
    ChillerTimeoutError,
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
    "ChillerTimeoutError",
    "CoolantProfile",
    "DISTILLED_WATER",
    "DeviceStatus",
    "ProtocolError",
    "ProtocolUnavailableError",
    "RecoveryPolicy",
    "SetpointValidationError",
    "SimulatedDevice",
    "TransportError",
]
