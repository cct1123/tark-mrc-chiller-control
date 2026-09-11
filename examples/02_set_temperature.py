"""Request an 18 Celsius target on the simulator, then read it back."""

from tark_chiller import Chiller, SimulatedDevice


def main() -> None:
    chiller = Chiller(SimulatedDevice())
    try:
        chiller.connect()
        chiller.set_setpoint(18.0)
        print(f"Reported setpoint: {chiller.get_setpoint():.2f} Celsius")
        print(f"Temperature: {chiller.get_temperature():.2f} Celsius")
        print("Temperature approaches the target gradually.")
    finally:
        chiller.disconnect()


if __name__ == "__main__":
    main()
