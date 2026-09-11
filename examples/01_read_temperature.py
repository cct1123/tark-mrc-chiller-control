"""Read one simulated temperature, target and status; no hardware is used."""

from tark_chiller import Chiller, Simulator


def main() -> None:
    with Chiller(Simulator()) as chiller:
        print(f"Temperature: {chiller.read_temperature():.2f} Celsius")
        print(f"Setpoint: {chiller.read_setpoint():.2f} Celsius")
        print(chiller.read_status())


if __name__ == "__main__":
    main()
