"""06 · Monitor chiller status and temperature. One device owner at a time."""

from math import isfinite
from time import monotonic, sleep

from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P

# 2. Choose the port and monitoring options.
PORT = None  # Set the identified serial port, e.g. "COM9".
DURATION_S = 60.0  # Use None to monitor until Ctrl+C / Interrupt Kernel.
INTERVAL_S = 1.0


def print_sample(sample):
    """Print one completed poll; failed readings are never presented as live."""
    stamp = sample["timestamp_utc"].isoformat(timespec="seconds")
    status = sample["status"]
    if sample["error"] or not status["connected"]:
        print(f"{stamp} | UNAVAILABLE | {sample['error'] or status['detail']}", flush=True)
        return
    active = ", ".join(name for name, value in status["alarms"].items() if value) or "none"
    print(
        f"{stamp} | {sample['temperature_c']:.2f} C | "
        f"target={sample['setpoint_c']:.2f} C | mode={status['mode']} | "
        f"chiller={'On' if status['chiller_on'] else 'Off'} | "
        f"dryer={'On' if status['dryer_on'] else 'Off'} | "
        f"summary_alarm={status['summary_alarm']} | active alarms: {active}",
        flush=True,
    )


# 3. Construct, connect, monitor and release. U can change AUTO START to RUN.
# Prepare the filled cooling loop. Closing software does not stop the pump.
if not isinstance(PORT, str) or not PORT.strip():
    raise ValueError('Hardware not configured: set PORT, e.g. "COM9". No port was opened.')
if DURATION_S is not None and (not isfinite(DURATION_S) or DURATION_S <= 0):
    raise ValueError("DURATION_S must be positive finite seconds or None")
if not isfinite(INTERVAL_S) or INTERVAL_S <= 0:
    raise ValueError("INTERVAL_S must be positive finite seconds")

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

with chiller:
    # The worker coordinates U/J/I/H0. Reading snapshot() adds no serial queries.
    # No target, mode or sensor-selection writes are enabled; no CSV is created.
    chiller.start_monitoring(interval_s=INTERVAL_S, history_size=300)
    deadline = None if DURATION_S is None else monotonic() + DURATION_S
    last_reported = float("-inf")
    print("Live monitoring: UTC | temperature | target | mode | on/off | alarms", flush=True)
    while True:
        snapshot = chiller.snapshot()
        for sample in snapshot["history"]:
            if sample["completed_monotonic"] > last_reported:
                print_sample(sample)
                last_reported = sample["completed_monotonic"]
        if snapshot["service_error"] or snapshot["failed_samples"] or not snapshot["running"]:
            raise RuntimeError(
                snapshot["service_error"] or "A monitoring poll failed; inspect the reported error"
            )
        if deadline is not None and monotonic() >= deadline:
            break
        sleep(min(0.1, INTERVAL_S))

    chiller.stop_monitoring()
    snapshot = chiller.snapshot()
    # Include a final poll that completed while stop_monitoring() was waiting.
    for sample in snapshot["history"]:
        if sample["completed_monotonic"] > last_reported:
            print_sample(sample)
            last_reported = sample["completed_monotonic"]
    if snapshot["service_error"] or snapshot["failed_samples"]:
        raise RuntimeError(snapshot["service_error"] or "A final monitoring poll failed")
    print(f"Monitoring complete: {snapshot['sample_count']} samples; no CSV written.")

# Duration expiry or a kernel interrupt releases the worker and serial port.
# The equipment retains its mode and target; disconnect is not a stop command.
print("Port closed. Follow the equipment procedure separately to stop the chiller/pump.")
