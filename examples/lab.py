"""Hardware examples: read, set, log, monitor or gui. No simulator fallback.

Configure the lab connection below only after obtaining and testing the matching
controller protocol. Construction and import open no port and start no worker.
"""

import argparse
from pathlib import Path
from time import monotonic, sleep

from tark_chiller import Chiller, ProtocolError
from tark_chiller.serial import Codec, RS485Mode, SerialDevice, SerialSettings

# Fill from the identified controller and adapter documentation; no Tark defaults.
SERIAL_SETTINGS: SerialSettings | None = None
CODEC_CLASS: type[Codec] | None = None  # A fresh protocol instance for each controller.
RS485_MODE: RS485Mode | None = None  # Native direction settings only if required.


def create_chiller() -> Chiller:
    """Return a disconnected hardware controller owned by the calling program."""
    if SERIAL_SETTINGS is None or CODEC_CLASS is None:
        raise ProtocolError(
            "Hardware not configured: edit examples/lab.py with verified serial settings "
            "and a documented codec class. The matching controller communication manual "
            "is still required. No port was opened."
        )
    return Chiller(SerialDevice(SERIAL_SETTINGS, codec=CODEC_CLASS(), rs485=RS485_MODE))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["read", "set", "log", "monitor", "gui"])
    parser.add_argument("target_c", nargs="?", type=float, help="Approved Celsius target for set")
    parser.add_argument("--csv", type=Path, help="New CSV file; required for log")
    args = parser.parse_args(argv)
    if (args.command == "set") != (args.target_c is not None):
        parser.error("Only set takes a target, and it requires an explicit Celsius value")
    if args.command == "log" and args.csv is None:
        parser.error("log requires --csv with a new output filename")
    if args.command in {"read", "set"} and args.csv is not None:
        parser.error("--csv is available with log, monitor or gui")
    if args.command == "gui":
        from tark_chiller.gui import create_app

    with create_chiller() as chiller:
        if args.command == "read":
            print(f"Temperature: {chiller.read_temperature():.2f} Celsius")
            print(f"Setpoint: {chiller.read_setpoint():.2f} Celsius")
            print(chiller.read_status())
            return
        if args.command == "set":
            print(f"Original setpoint: {chiller.read_setpoint():.2f} Celsius")
            chiller.set_setpoint(args.target_c)
            reported_c = chiller.read_setpoint()
            print(f"Reported setpoint: {reported_c:.2f} Celsius")
            if reported_c != args.target_c:
                raise RuntimeError(
                    f"Target not confirmed: requested {args.target_c:g} Celsius, "
                    f"device reported {reported_c:g} Celsius. No write was retried."
                )
            print("Readback matches the request. The liquid may not have reached the target.")
            return

        monitor = chiller.start_monitoring(csv_path=args.csv)
        try:
            if args.command == "gui":
                app = create_app(chiller, monitor)
                app.run(host="127.0.0.1", port=8050, debug=False, use_reloader=False)
            else:
                deadline = monotonic() + 5 if args.command == "log" else None
                while deadline is None or monotonic() < deadline:
                    snapshot = monitor.snapshot()
                    if snapshot.service_error or snapshot.logging_error or not snapshot.running:
                        raise RuntimeError(
                            snapshot.service_error or snapshot.logging_error or "Monitoring stopped"
                        )
                    if args.command == "monitor" and snapshot.latest:
                        sample = snapshot.latest
                        if sample.error:
                            print(f"Read failed: {sample.error}")
                        elif sample.temperature_c is not None:
                            print(f"Temperature: {sample.temperature_c:.2f} Celsius")
                    sleep(1)
        except KeyboardInterrupt:
            print("Stopping monitoring.")
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(f"Samples: {snapshot.sample_count}; saved rows: {snapshot.logged_samples}")
    print(f"Failed polls: {snapshot.failed_samples}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
