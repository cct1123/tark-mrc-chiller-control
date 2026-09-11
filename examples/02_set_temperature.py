"""Send one operator-selected Celsius setpoint after approved read-only validation."""

import argparse

from connection import create_chiller


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_c", type=float, help="Approved target in degrees Celsius")
    args = parser.parse_args(argv)
    with create_chiller() as chiller:
        print(f"Original setpoint: {chiller.read_setpoint():.2f} Celsius")
        chiller.set_setpoint(args.target_c)
        reported_c = chiller.read_setpoint()
        print(f"Reported setpoint: {reported_c:.2f} Celsius")
        if reported_c != args.target_c:
            raise RuntimeError(
                f"Target not confirmed: requested {args.target_c:g} Celsius, "
                f"device reported {reported_c:g} Celsius. No write was retried."
            )
        print(f"Temperature: {chiller.read_temperature():.2f} Celsius")
        print("Readback matches the requested target. The liquid may not have reached it.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
