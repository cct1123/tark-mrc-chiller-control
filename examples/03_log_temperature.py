"""Record five seconds of physical chiller readings without changing its target."""

from time import sleep

from connection import create_chiller


def main() -> None:
    with create_chiller() as chiller:
        monitor = chiller.start_monitoring(csv_path="outputs/03_temperature.csv")
        try:
            sleep(5)
        except KeyboardInterrupt:
            print("Stopping recording.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(f"Saved {snapshot.logged_samples} rows to outputs/03_temperature.csv")
    print(f"Failed polls: {snapshot.failed_samples}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
