"""Edit this file once for your lab's documented controller and serial adapter.

No MRC protocol codec is supplied yet. Leave the entries unset until the matching
communication manual has been obtained and its commands implemented and tested.
These are configuration values; no device is created or opened at import time.
"""

from tark_chiller import Chiller, ProtocolUnavailableError
from tark_chiller.serial import Codec, RS485Mode, SerialDevice, SerialSettings

# Required: fill from the controller manual and the identified operating-system port.
SERIAL_SETTINGS: SerialSettings | None = None
# Set this to the implemented codec class. Each chiller gets its own codec instance.
CODEC_CLASS: type[Codec] | None = None

# None leaves native RS-485 direction mode unset. Follow the verified adapter setup.
RS485_MODE: RS485Mode | None = None


def create_chiller() -> Chiller:
    """Create a disconnected hardware controller; the caller owns its lifetime."""
    if SERIAL_SETTINGS is None or CODEC_CLASS is None:
        raise ProtocolUnavailableError(
            "Hardware not configured: edit examples/connection.py with verified serial "
            "settings and a documented protocol codec. The matching controller "
            "communication manual is still required. No port was opened."
        )
    device = SerialDevice(SERIAL_SETTINGS, codec=CODEC_CLASS(), rs485=RS485_MODE)
    return Chiller(device)
