"""Watch hardware telemetry in a read-only dashboard until Ctrl+C."""

from hardware import create_chiller

from tark_chiller.gui import create_app

INTERVAL_S = 1.0
CSV_PATH = None  # Optional new path, e.g. "outputs/dashboard_session.csv".

# 1. Connect the hardware and start acquisition explicitly. No target writes.
with create_chiller() as chiller:
    monitor = chiller.start_monitoring(interval_s=INTERVAL_S, csv_path=CSV_PATH)

    # 2. Pass existing objects to the app; it does not create another worker.
    # Keep both the driver and dashboard read-only while learning the API.
    app = create_app(chiller, monitor, allow_setpoints=False)
    print("Open http://127.0.0.1:8050 in your browser. Press Ctrl+C to exit.")
    try:
        # 3. Disable the development reloader to keep one owner of the port.
        app.run(host="127.0.0.1", port=8050, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("Stopping dashboard and acquisition.")

# 4. Context exit joins acquisition, closes CSV and releases the port.
# Closing only the browser does none of those things; the host keeps running.
snapshot = monitor.snapshot()
if snapshot.service_error or snapshot.logging_error:
    raise RuntimeError(snapshot.service_error or snapshot.logging_error)
# Equipment is not stopped and its target is not restored automatically.
