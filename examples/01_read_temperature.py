"""Read a simulated chiller once. No physical device is used."""

from tark_chiller import Chiller, SimulatedDevice


def main() -> None:
    chiller = Chiller(SimulatedDevice())
    try:
        chiller.connect()
        print(f"Temperature: {chiller.get_temperature():.2f} Celsius")
        print(f"Setpoint: {chiller.get_setpoint():.2f} Celsius")
        print(chiller.get_status())
    finally:
        chiller.disconnect()


if __name__ == "__main__":
    main()
