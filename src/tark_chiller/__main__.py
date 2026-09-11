"""Run the optional simulator dashboard or a headless recording session."""

import argparse
import signal
import time
from threading import current_thread, main_thread
from types import FrameType

from . import Chiller, Simulator
from .monitor import _seconds


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true", help="Monitor without a browser")
    parser.add_argument("--duration", type=float, help="Headless seconds; omit to run until Ctrl+C")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    parser.add_argument("--csv", help="New output CSV path; existing files are never overwritten")
    parser.add_argument("--history", type=int, default=3600, help="Maximum retained samples")
    parser.add_argument(
        "--reconnect-attempts",
        type=int,
        default=0,
        help="Read reconnect attempts per outage (0–10); never retries writes",
    )
    args = parser.parse_args(argv)
    try:
        _seconds(args.interval, "interval")
        if args.duration is not None:
            _seconds(args.duration, "duration")
            if not args.headless:
                raise ValueError("--duration requires --headless")
        chiller = Chiller(Simulator(), reconnect_attempts=args.reconnect_attempts)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.headless:
        try:
            from .gui import create_app
        except ImportError as exc:
            parser.error(
                f'Dashboard unavailable: {exc}. Install with: python -m pip install ".[gui]"'
            )
    previous_handlers = {}
    monitor = None
    try:
        if current_thread() is main_thread():
            for name in ("SIGTERM", "SIGBREAK"):
                if (sig := getattr(signal, name, None)) is not None:
                    previous_handlers[sig] = signal.signal(sig, _interrupt)
        chiller.connect()
        monitor = chiller.start_monitoring(
            interval_s=args.interval,
            csv_path=args.csv,
            history_size=args.history,
        )
        if args.headless:
            print("Simulator monitoring started. Press Ctrl+C to stop.", flush=True)
            deadline = time.monotonic() + args.duration if args.duration is not None else None
            while monitor.snapshot().running:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        else:
            create_app(chiller, monitor, stale_after_s=max(3.0, args.interval * 3)).run(
                host="127.0.0.1",
                port=8050,
                debug=False,
                use_reloader=False,
            )
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    finally:
        try:
            chiller.disconnect()
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    if monitor is not None:
        snapshot = monitor.snapshot()
        print(f"Stopped simulator; {len(snapshot.history)} samples retained.")
        if snapshot.service_error or snapshot.logging_error:
            raise SystemExit(snapshot.service_error or snapshot.logging_error)


if __name__ == "__main__":
    main()
