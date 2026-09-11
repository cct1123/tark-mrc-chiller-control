"""Request an 18 Celsius simulator target and read it back."""

from tark_chiller import Chiller, Simulator


def main() -> None:
    with Chiller(Simulator()) as chiller:
        chiller.set_setpoint(18.0)
        print(f"Reported setpoint: {chiller.read_setpoint():.2f} Celsius")
        print(f"Temperature: {chiller.read_temperature():.2f} Celsius")
        print("Temperature approaches the target gradually.")


if __name__ == "__main__":
    main()
