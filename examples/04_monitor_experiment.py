"""Monitor and record ten seconds of simulated cooling in the background."""

from time import monotonic, sleep

from tark_chiller import Chiller, CsvLogger, Monitor, SimulatedDevice


def main() -> None:
    chiller = Chiller(SimulatedDevice())
    logger = CsvLogger("outputs/04_experiment.csv")
    monitor = Monitor(chiller, interval_s=0.5, logger=logger)
    try:
        chiller.connect()
        chiller.set_setpoint(18.0)
        monitor.start()
        deadline = monotonic() + 10
        while monotonic() < deadline:
            snapshot = monitor.state.snapshot()
            if not snapshot.running or snapshot.logging_error:
                raise RuntimeError(snapshot.service_error or snapshot.logging_error)
            if snapshot.latest:
                sample = snapshot.latest
                print(f"Samples: {snapshot.sample_count}; Celsius: {sample.temperature_c}")
                if sample.error:
                    print(sample.error)
            sleep(1)
    except KeyboardInterrupt:
        print("Stopping experiment.")
    finally:
        monitor.request_stop()
        try:
            chiller.disconnect()
        finally:
            monitor.stop()
            logger.close()
    snapshot = monitor.state.snapshot()
    if error := snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(error)
    print(f"Saved {logger.rows_written} rows to outputs/04_experiment.csv")


if __name__ == "__main__":
    main()
