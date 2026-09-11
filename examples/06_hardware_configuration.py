"""Configuration template only: no controller protocol or serial defaults.

Supply settings from the matching controller/adapter documentation and verified
OS port. Construction opens nothing; connect refuses while no codec is supplied.
"""

from tark_chiller import Chiller
from tark_chiller.serial import RS485Mode, SerialDevice, SerialSettings


def configure(settings: SerialSettings, *, rs485: RS485Mode | None = None) -> Chiller:
    """Build a disconnected driver with deliberately unavailable protocol."""
    return Chiller(SerialDevice(settings, rs485=rs485))


if __name__ == "__main__":
    raise SystemExit(
        "Hardware unavailable: Controller communication manual is required; "
        "no documented codec is configured."
    )
