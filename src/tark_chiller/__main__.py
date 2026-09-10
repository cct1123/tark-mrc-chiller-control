"""Simulator-only application; lifecycle ownership stays outside Dash."""

import argparse
import time

from .api import Chiller
from .csvlog import CsvLogger
from .monitoring import LiveState, Monitor, positive_seconds
from .simulator import SimulatedDevice


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, default=10.0, help="Headless run seconds")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--csv", help="New output CSV path; existing files are refused")
    args = parser.parse_args(argv)
    try:
        positive_seconds(args.duration, "duration")
        positive_seconds(args.interval, "interval")
    except ValueError as exc:
        parser.error(str(exc))
    chiller = Chiller(SimulatedDevice())
    state = LiveState()
    logger = CsvLogger(args.csv) if args.csv else None
    monitor = Monitor(chiller, state, interval_s=args.interval, logger=logger)
    try:
        chiller.connect()
        monitor.start()
        if args.headless:
            time.sleep(args.duration)
        else:
            from .gui import create_app

            create_app(chiller, state, stale_after_s=max(3.0, args.interval * 3)).run(
                host="127.0.0.1",
                port=8050,
                debug=False,
                use_reloader=False,
            )
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        try:
            chiller.disconnect()
        finally:
            if logger is not None:
                logger.close()
    snapshot = state.snapshot()
    print(f"Stopped simulator; {len(snapshot.history)} samples retained.")
    if snapshot.service_error or snapshot.logging_error:
        raise SystemExit(snapshot.service_error or snapshot.logging_error)


if __name__ == "__main__":
    main()
