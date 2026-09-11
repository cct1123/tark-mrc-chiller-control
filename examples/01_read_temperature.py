"""Read temperature, setpoint and status from the configured physical chiller."""

from connection import create_chiller

from tark_chiller import ProtocolError


def main() -> None:
    with create_chiller() as chiller:
        print(f"Temperature: {chiller.read_temperature():.2f} Celsius")
        print(f"Setpoint: {chiller.read_setpoint():.2f} Celsius")
        print(chiller.read_status())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ProtocolError) as error:
        raise SystemExit(str(error)) from error
