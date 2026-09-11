"""Hardware-independent core API; Dash and pySerial are optional dependencies."""

from .api import Chiller, RecoveryPolicy
from .csvlog import CsvLogger
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
from .monitoring import LiveState, Monitor
from .safety import DISTILLED_WATER, CoolantProfile
from .simulator import SimulatedDevice

__all__ = [
    "Chiller",
    "ChillerConnectionError",
    "ChillerDevice",
    "ChillerError",
    "ChillerTimeoutError",
    "CoolantProfile",
    "CsvLogger",
    "DISTILLED_WATER",
    "DeviceStatus",
    "LiveState",
    "Monitor",
    "ProtocolError",
    "ProtocolUnavailableError",
    "RecoveryPolicy",
    "SetpointValidationError",
    "SimulatedDevice",
    "TransportError",
]
