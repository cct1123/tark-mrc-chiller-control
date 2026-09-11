"""Record five seconds of simulator data in a new CSV file."""

from time import sleep

from tark_chiller import Chiller, Simulator


def main() -> None:
    with Chiller(Simulator()) as chiller:
        monitor = chiller.start_monitoring(csv_path="outputs/03_temperature.csv")
        try:
            sleep(5)
        except KeyboardInterrupt:
            print("Stopping recording.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(f"Saved {snapshot.logged_samples} rows to outputs/03_temperature.csv")


if __name__ == "__main__":
    main()
