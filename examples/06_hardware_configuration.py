"""Configuration template only: the missing MRC protocol prevents connection.

Supply SerialSettings from the matching controller manual and verified port.
For RS-485, also supply RS485Mode for the verified adapter. There are deliberately
no example baud rates, pin levels or addresses. This script never opens a port.
"""

from tark_chiller import Chiller
from tark_chiller.errors import ProtocolUnavailableError
from tark_chiller.hardware import SerialDevice
from tark_chiller.protocol import MissingProtocol
from tark_chiller.transport import RS232Transport, RS485Mode, RS485Transport, SerialSettings


def configure(settings: SerialSettings, *, rs485_mode: RS485Mode | None = None) -> Chiller:
    """Build the common API without connecting; its default codec refuses hardware."""
    transport = (
        RS232Transport(settings)
        if rs485_mode is None
        else RS485Transport(settings, mode=rs485_mode)
    )
    return Chiller(SerialDevice(transport))


if __name__ == "__main__":
    try:
        MissingProtocol().ensure_available()
    except ProtocolUnavailableError as exc:
        raise SystemExit(f"Hardware unavailable: {exc}") from None
