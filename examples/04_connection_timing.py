"""04 · Measure connection timing. Run one device owner at a time."""

from time import perf_counter

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

print(f"Port: {chiller.settings['port']}; transaction timeout: {chiller.timeout_s:g} s")
assert not chiller.is_connected

connect_ok = disconnect_ok = False
try:
    started = perf_counter()
    try:
        chiller.connect()
        connect_ok = True
    finally:
        connect_ms = (perf_counter() - started) * 1000
        print(f'connect():    {connect_ms:10.3f} ms | success={connect_ok}')
    assert chiller.is_connected
finally:
    started = perf_counter()
    try:
        chiller.disconnect()
        disconnect_ok = True
    finally:
        disconnect_ms = (perf_counter() - started) * 1000
        print(f'disconnect(): {disconnect_ms:10.3f} ms | success={disconnect_ok}')

assert not chiller.is_connected
print('Connection cycle complete; serial port released.')
