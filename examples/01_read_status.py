"""Connect to your hardware and read temperatures and controller status."""

from hardware import create_chiller

# 1. Configure hardware.py first. Construction does not open the port.
chiller = create_chiller()
try:
    # T255P connect sends U: it takes serial control and can start AUTO START.
    # CAL connect checks the controller identity, RTD input and Celsius units.
    chiller.connect()

    # 2. Query the measured temperature and target separately, in Celsius.
    # Compare these values with the front panel and your temperature reference.
    print(f"Temperature:       {chiller.read_temperature():.2f} C")
    print(f"Reported setpoint: {chiller.read_setpoint():.2f} C")

    # 3. Inspect diagnostics. CAL detail includes actual model/firmware codes;
    # T255P detail includes mode/alarm flags, but cannot identify the unit.
    status = chiller.read_status()
    print(f"Connected: {status.connected}")
    print(f"Backend:   {status.backend}")
    print(f"Details:   {status.detail}")
finally:
    # 4. Release the port even after a failed read. This does not stop equipment.
    chiller.disconnect()
