"""03 · Record temperatures to CSV. Run one device owner at a time."""

from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic, sleep

from tark_chiller.controller import T255P, TarkChiller

# 2. Choose the port and operation options
PORT = None  # Set the identified serial port, e.g. "COM9".

DURATION_S = 10.0
INTERVAL_S = 1.0

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

if not isfinite(DURATION_S) or DURATION_S <= 0:
    raise ValueError("DURATION_S must be a positive finite number of seconds")

# 1. Choose a unique UTC filename. Existing files are never overwritten.
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
csv_path = Path("outputs") / f"hardware_{stamp}.csv"

with chiller:
    # 2. Start one acquisition worker. It reads temperature, target and status,
    # flushes CSV rows and retains bounded history for snapshot() readers.
    chiller.start_monitoring(interval_s=INTERVAL_S, csv_path=csv_path)
    deadline = monotonic() + DURATION_S
    while monotonic() < deadline:
        snapshot = chiller.snapshot()  # Memory only; no extra device query.
        if snapshot["service_error"] or snapshot["logging_error"] or (not snapshot["running"]):
            raise RuntimeError(
                snapshot["service_error"] or snapshot["logging_error"] or "Monitoring stopped"
            )
        if snapshot["failed_samples"]:
            raise RuntimeError("A hardware poll failed. Inspect the CSV error column and device.")
        sleep(0.1)
    # 3. Finish recording before inspecting the final counters. If the loop
    # raises, the controller context cleans up and preserves that error.
    chiller.stop_monitoring()

    # Catch a failure in the last in-flight poll or while closing the CSV, too.
    snapshot = chiller.snapshot()
    if snapshot["service_error"] or snapshot["logging_error"] or snapshot["failed_samples"]:
        raise RuntimeError(
            snapshot["service_error"] or snapshot["logging_error"] or "A hardware poll failed"
        )
    print(f"Samples: {snapshot['sample_count']}; saved rows: {snapshot['logged_samples']}")
    print(f"CSV saved to: {csv_path.resolve()}")

# Rows include UTC time, elapsed seconds, Celsius readings, status and errors.
# Failed measurements are blank. Ending acquisition does not stop equipment.
