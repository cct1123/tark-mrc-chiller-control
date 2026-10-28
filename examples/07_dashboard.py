"""07 · Monitor structured status and request targets/modes. One owner per port."""

import logging

from tark_chiller.controller import TarkChiller
from tark_chiller.gui import run_dashboard
from tark_chiller.protocols import T255P

# 2. Choose the port and operation options
PORT = "COM9"  # Set the identified serial port, e.g. "COM9".

INTERVAL_S = 1.0
CSV_PATH = None  # Optional NEW path, e.g. "outputs/dashboard_session.csv".
HOST = "127.0.0.1"
PORT_HTTP = 8006
SERIAL_TRACE = True  # Enable exact TX/RX logging when investigating device errors.
ALLOW_SETPOINTS = True
ALLOW_MODE_CHANGES = True

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # Keep successful HTTP refreshes quiet.
if SERIAL_TRACE:
    logging.getLogger("tark_chiller.controller").setLevel(logging.DEBUG)

# 3. Construct without connecting. Use the GUI buttons to connect and monitor.
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
    protocol=T255P(allow_writes=ALLOW_SETPOINTS or ALLOW_MODE_CHANGES),
    setpoint_range=(5.0, 45.0),
    coolant_source="ThermoTek T255P Rev C pp9/20; verify installed distilled water",
    # Recover failed reads, including bad-command replies, with a fresh handshake.
    # Failed polls stay in the recording; monitoring continues. Writes run once.
    reconnect_attempts=2,
    reconnect_delay_s=0.5,
)

print(f"Open http://{HOST}:{PORT_HTTP}. Use Connect, then Start monitoring.")
print("Equipment mode, on/off states and six alarm flags come from monitoring snapshots.")
print("Use Shutdown GUI or press Ctrl+C in this terminal to stop.")
try:
    run_dashboard(
        chiller,
        host=HOST,
        port=PORT_HTTP,
        interval_s=INTERVAL_S,
        csv_path=CSV_PATH,
        allow_setpoints=ALLOW_SETPOINTS,
        allow_mode_changes=ALLOW_MODE_CHANGES,
    )
except KeyboardInterrupt:
    print("Stopped dashboard and acquisition.")

snapshot = chiller.snapshot()
if snapshot["service_error"] or snapshot["logging_error"]:
    raise RuntimeError(snapshot["service_error"] or snapshot["logging_error"])
print("Dashboard stopped; acquisition and connection closed.")
