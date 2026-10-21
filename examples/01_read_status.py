"""01 · Read temperature and status. Run one device owner at a time."""

from tark_chiller.controller import T255P, TarkChiller

# 2. Choose the port and operation options
PORT = None  # Set the identified serial port, e.g. "COM9".

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
    protocol=T255P(allow_writes=False),
    setpoint_range=(5.0, 45.0),
    coolant_source="ThermoTek T255P Rev C pp9/20; verify installed distilled water",
)

# T255P connect sends U: it takes serial control and can start AUTO START.
with chiller:
    # 2. Query the measured temperature and target separately, in Celsius.
    # Compare these values with the front panel and your temperature reference.
    print(f"Temperature:       {chiller.read_temperature():.2f} C")
    print(f"Reported setpoint: {chiller.read_setpoint():.2f} C")

    # 3. Inspect mode and alarm diagnostics; these do not identify the unit.
    status = chiller.read_status()
    print(f"Connected: {status['connected']}")
    print(f"Backend:   {status['backend']}")
    print(f"Details:   {status['detail']}")

# 4. Context exit releases the port, including after a failed read. If cleanup
# also fails, the original error is preserved with a cleanup note. Retry cleanup
# as needed; closing the software connection does not stop equipment.
