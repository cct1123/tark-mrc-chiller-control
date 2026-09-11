"""Write five simulator samples to a new CSV in the current folder."""

from time import sleep

from tark_chiller import Chiller, CsvLogger, Monitor, SimulatedDevice


def main() -> None:
    chiller = Chiller(SimulatedDevice())
    with CsvLogger("outputs/03_temperature.csv") as logger:
        monitor = Monitor(chiller, logger=logger)
        try:
            chiller.connect()
            for _ in range(5):
                sample = monitor.poll_once()
                if error := monitor.state.snapshot().logging_error:
                    raise RuntimeError(error)
                print(sample.timestamp_utc.isoformat(), sample.temperature_c, sample.error)
                sleep(1)
        finally:
            chiller.disconnect()
        print(f"Saved {logger.rows_written} rows to outputs/03_temperature.csv")


if __name__ == "__main__":
    main()
