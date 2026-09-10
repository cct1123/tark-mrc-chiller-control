"""Simulator-only application; lifecycle ownership stays outside Dash."""

import argparse
import time

from .api import Chiller, RecoveryPolicy
from .csvlog import CsvLogger
from .errors import ChillerError
from .monitoring import LiveState, Monitor, positive_seconds
from .simulator import SimulatedDevice


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, default=10.0, help="Headless run seconds")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--csv", help="Output CSV path; existing files require --append-csv")
    parser.add_argument("--append-csv", action="store_true", help="Validate and resume a CSV")
    parser.add_argument("--history", type=int, default=3600, help="Maximum retained samples")
    parser.add_argument(
        "--reconnect-attempts",
        type=int,
        default=0,
        help="Opt-in reconnect budget per outage (0–10); never retries writes",
    )
    parser.add_argument(
        "--noise", type=float, default=0, help="Simulation measurement noise SD (°C)"
    )
    parser.add_argument("--seed", type=int, default=0, help="Simulation noise seed")
    parser.add_argument(
        "--simulation-interval",
        type=float,
        default=0,
        help="Simulation measurement interval (seconds, 0 means continuous)",
    )
    args = parser.parse_args(argv)
    try:
        positive_seconds(args.duration, "duration")
        positive_seconds(args.interval, "interval")
        if args.append_csv and not args.csv:
            raise ValueError("--append-csv requires --csv")
        chiller = Chiller(
            SimulatedDevice(
                noise_std_c=args.noise,
                seed=args.seed,
                measurement_interval_s=args.simulation_interval,
            ),
            recovery=RecoveryPolicy(args.reconnect_attempts),
        )
        state = LiveState(capacity=args.history)
    except (ValueError, ChillerError) as exc:
        parser.error(str(exc))
    try:
        logger = CsvLogger(args.csv, append=args.append_csv) if args.csv else None
    except (OSError, ValueError) as exc:
        parser.error(f"Cannot open CSV: {exc}")
    monitor = Monitor(chiller, state, interval_s=args.interval, logger=logger)
    try:
        chiller.connect()
        monitor.start()
        if args.headless:
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline and state.snapshot().running:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
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
        monitor.request_stop()
        # Disconnect publishes cancellation before waiting for the API lock,
        # interrupting recovery delays. Its close is serialized with active I/O.
        # Keep the logger open until the monitor has published its final sample.
        try:
            chiller.disconnect()
        finally:
            monitor.stop()
            if logger is not None:
                logger.close()
    snapshot = state.snapshot()
    print(f"Stopped simulator; {len(snapshot.history)} samples retained.")
    if snapshot.service_error or snapshot.logging_error:
        raise SystemExit(snapshot.service_error or snapshot.logging_error)


if __name__ == "__main__":
    main()
