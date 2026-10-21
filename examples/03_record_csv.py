"""Record hardware temperatures to a new CSV for a fixed duration."""

from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic, sleep

from hardware import create_chiller

DURATION_S = 10.0
INTERVAL_S = 1.0  # Poll start interval; serial I/O may make a poll take longer.

if not isfinite(DURATION_S) or DURATION_S <= 0:
    raise ValueError("DURATION_S must be a positive finite number of seconds")

# 1. Choose a unique UTC filename. Existing files are never overwritten.
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
csv_path = Path("outputs") / f"hardware_{stamp}.csv"

with create_chiller() as chiller:
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
