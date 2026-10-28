"""02 · Set one temperature target. Run one device owner at a time."""

from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P, ProtocolError

# 2. Choose the port and operation options
PORT = None  # Set the identified serial port, e.g. "COM9".

TARGET_C = 20.0  # Choose an approved target within your setup limits.

# 3. Construct, connect, perform the operation and release.
# T255P connection takes serial control and can start AUTO START. Prepare
# the cooling loop first. Disconnect does not stop the chiller or pump.
if not isinstance(PORT, str) or not PORT.strip():
    raise ValueError('Hardware not configured: set PORT, e.g. "COM9". No port was opened.')

chiller = TarkChiller(
    {
        "port": PORT,
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "xonxoff": True,
        "rtscts": False,
        "dsrdtr": False,
        "rts": False,
        "dtr": False,
    },
    protocol=T255P(allow_writes=True),
    setpoint_range=(5.0, 45.0),
    coolant_source="ThermoTek T255P Rev C pp9/20; verify installed distilled water",
)

with chiller:
    before = chiller.get_memory()  # H0 only; context entry established the session with U.
    print(f"Existing setpoint: {before['setpoint_c']:.2f} C")
    print(f"Applying {TARGET_C:g} C")

    # M only: validate this one acknowledgement; never retry an uncertain write.
    chiller.set_setpoint(TARGET_C)
    print("Target acknowledgement received.")

    # 3. Read the target again. This is a target check, not a settling test.
    # H0 is a separate command. A failure here does not authorize resending M.
    try:
        reported_c = chiller.get_memory()["setpoint_c"]
    except BaseException as error:
        error.add_note("Target acknowledged; readback failed. Inspect the panel; do not replay M.")
        raise
    print(f"Reported setpoint: {reported_c:.2f} C")
    if reported_c != TARGET_C:
        raise ProtocolError(
            "Target readback differs from request; inspect the panel, do not replay"
        )
    print("Target readback verified. Observe the actual liquid temperature separately.")

# 4. The connection is closed; the equipment keeps its current target/state.
# No automatic restoration, pump control or equipment stop is sent.
