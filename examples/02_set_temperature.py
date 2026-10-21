"""02 · Set one temperature target. Run one device owner at a time."""

from tark_chiller.controller import T255P, TarkChiller

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
    print(f"Existing setpoint: {chiller.read_setpoint():.2f} C")
    input(f"Press Enter to apply {TARGET_C:g} C; Ctrl+C to cancel. ")

    # 2. Send one request. Values are checked; uncertain writes are never retried.
    # T255P requires RUN with no alarms and suitable coolant/setup.
    chiller.set_setpoint(TARGET_C)

    # 3. Read the target again. This is a target check, not a settling test.
    reported_c = chiller.read_setpoint()
    print(f"Reported setpoint: {reported_c:.2f} C")
    if reported_c != TARGET_C:
        raise RuntimeError("Target differs from the request. Inspect the equipment; do not retry.")
    print("Readback matches. Observe the actual liquid temperature separately.")

# 4. The connection is closed; the equipment keeps its current target/state.
# No automatic restoration, pump control or equipment stop is sent.
