"""Monitor the physical chiller until Ctrl+C; print readings and save CSV."""

from time import sleep

from connection import create_chiller


def main() -> None:
    with create_chiller() as chiller:
        monitor = chiller.start_monitoring(csv_path="outputs/04_experiment.csv")
        try:
            while True:
                snapshot = monitor.snapshot()
                if snapshot.service_error or snapshot.logging_error or not snapshot.running:
                    raise RuntimeError(
                        snapshot.service_error or snapshot.logging_error or "Monitoring stopped"
                    )
                if snapshot.latest:
                    sample = snapshot.latest
                    if sample.error:
                        print(f"Read failed: {sample.error}")
                    elif sample.temperature_c is not None:
                        print(f"Temperature: {sample.temperature_c:.2f} Celsius")
                sleep(1)
        except KeyboardInterrupt:
            print("Stopping experiment.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(f"Saved {snapshot.logged_samples} rows to outputs/04_experiment.csv")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
