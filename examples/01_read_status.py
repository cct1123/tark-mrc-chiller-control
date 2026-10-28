"""01 · Read temperature and status. Run one device owner at a time."""

from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P, ProtocolError

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
    # Each get_ call sends exactly one command. U refreshes the session;
    # J reads alarms, I reads temperature, and H0 reads target/max-power memory.
    status = chiller.get_status()  # U
    alarms = chiller.get_alarms()  # J
    print(f"Connected: {status['connected']}")
    print(f"Backend:   {status['backend']}")
    print(f"Mode:      {status['mode']} (code {status['mode_code']})")
    print(f"Chiller on: {status['chiller_on']}; dryer on: {status['dryer_on']}")
    print(f"Alarm summary: {status['summary_alarm']}")
    for name, active in alarms.items():
        print(f"  {name}: {'ACTIVE' if active else 'inactive'}")
    print(f"Details:   {status['detail']}")
    # A direct I query does not check J; make that decision explicitly here.
    if alarms["sensor"]:
        raise ProtocolError("T255P sensor alarm; temperature unavailable")
    temperature_c = chiller.get_temperature()  # I
    memory = chiller.get_memory()  # H0
    print(f"Temperature:       {temperature_c:.2f} C")
    print(f"Reported setpoint: {memory['setpoint_c']:.2f} C")
    print(f"Maximum-power setting (raw): {memory['max_power']}")
    # Compare Celsius readings with the front panel and your reference.

# 4. Context exit releases the port, including after a failed read. If cleanup
# also fails, the original error is preserved with a cleanup note. Retry cleanup
# as needed; closing the software connection does not stop equipment.
