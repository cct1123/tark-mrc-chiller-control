"""Display the configured physical chiller and record readings until Ctrl+C."""

from connection import create_chiller

from tark_chiller.gui import create_app


def main() -> None:
    with create_chiller() as chiller:
        monitor = chiller.start_monitoring(csv_path="outputs/05_dashboard.csv")
        try:
            app = create_app(chiller, monitor)
            app.run(host="127.0.0.1", port=8050, debug=False, use_reloader=False)
        except KeyboardInterrupt:
            print("Stopping dashboard.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
