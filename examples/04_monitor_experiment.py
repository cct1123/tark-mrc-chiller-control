"""Monitor ten seconds of simulated cooling; report progress and save CSV."""

from time import monotonic, sleep

from tark_chiller import Chiller, Simulator


def main() -> None:
    with Chiller(Simulator()) as chiller:
        chiller.set_setpoint(18.0)
        monitor = chiller.start_monitoring(interval_s=0.5, csv_path="outputs/04_experiment.csv")
        try:
            deadline = monotonic() + 10
            while monotonic() < deadline:
                snapshot = monitor.snapshot()
                if snapshot.service_error or snapshot.logging_error or not snapshot.running:
                    raise RuntimeError(
                        snapshot.service_error or snapshot.logging_error or "Monitoring stopped"
                    )
                if snapshot.latest:
                    sample = snapshot.latest
                    print(f"Samples: {snapshot.sample_count}; Celsius: {sample.temperature_c}")
                    if sample.error:
                        print(sample.error)
                sleep(1)
        except KeyboardInterrupt:
            print("Stopping experiment.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(f"Saved {snapshot.logged_samples} rows to outputs/04_experiment.csv")


if __name__ == "__main__":
    main()
