"""00 · Discover ports and connect. Run one device owner at a time."""

from serial.tools import list_ports

from tark_chiller.controller import T255P, TarkChiller

# Discover serial adapters without probing
ports = sorted(list_ports.comports(), key=lambda port: port.device)
print("Available serial ports (no devices probed):")
for port in ports:
    print(f"  {port.device}: {port.description}")
    print(f"    Hardware ID: {port.hwid}")
if not ports:
    print("  No serial ports reported by the operating system.")

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

print(f"Connecting to configured port: {chiller.settings['port']}")
with chiller:
    print(f"Connected: {chiller.is_connected}")
print(f"Connected after cleanup: {chiller.is_connected}")
