"""Shared hardware setup for the four tutorials. Importing opens nothing."""

from typing import Literal

from tark_chiller import ProtocolError, TarkChiller
from tark_chiller.serial import T255P, Cal33xx, RS485Mode, SerialDevice, SerialSettings

# Enter values from your identified unit and adapter; see examples/README.md.
DEVICE_MODEL: Literal["cal", "t255p"] | None = None
SERIAL_SETTINGS: SerialSettings | None = None
CAL_ADDRESS: int | None = None  # CAL only: actual address, 1–247.
EXPECTED_MODEL: int | None = None  # CAL writes: exact codes from tutorial 01.
EXPECTED_FIRMWARE: int | None = None
RS485_MODE: RS485Mode | None = None  # CAL only, if the adapter needs native direction control.


def create_chiller(*, allow_writes: bool = False) -> TarkChiller:
    """Build a disconnected driver. Only tutorial 02 enables target writes."""
    if SERIAL_SETTINGS is None or DEVICE_MODEL not in ("cal", "t255p"):
        raise ProtocolError(
            "Hardware not configured: edit examples/hardware.py with the identified model "
            "and verified serial settings. No port was opened."
        )
    if DEVICE_MODEL == "t255p":
        if CAL_ADDRESS is not None or EXPECTED_MODEL is not None or EXPECTED_FIRMWARE is not None:
            raise ProtocolError("T255P does not use CAL identity/address settings; clear them")
        return TarkChiller(
            SerialDevice(SERIAL_SETTINGS, codec=T255P(allow_writes), rs485=RS485_MODE),
            setpoint_range=(5.0, 45.0),
            coolant_source="ThermoTek T255P Rev C pp9/20; verify installed distilled water",
        )
    if CAL_ADDRESS is None:
        raise ProtocolError(
            "Hardware not configured: enter the actual CAL_ADDRESS in examples/hardware.py. "
            "No port was opened."
        )
    protocol = Cal33xx(CAL_ADDRESS, allow_writes, EXPECTED_MODEL, EXPECTED_FIRMWARE)
    return TarkChiller(SerialDevice(SERIAL_SETTINGS, codec=protocol, rs485=RS485_MODE))
