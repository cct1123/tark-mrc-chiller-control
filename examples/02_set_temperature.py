"""Apply one hardware temperature target and compare its reported readback."""

from hardware import create_chiller

TARGET_C = 20.0  # Edit for your experiment: CAL water 2–40 C; T255P water 5–45 C.

# 1. Explicitly enable writes for this tutorial. CAL also needs the actual
# EXPECTED_MODEL / EXPECTED_FIRMWARE codes from tutorial 01 in hardware.py.
with create_chiller(allow_writes=True) as chiller:
    print(f"Existing setpoint: {chiller.read_setpoint():.2f} C")
    input(f"Press Enter to apply {TARGET_C:g} C; Ctrl+C to cancel. ")

    # 2. Send one request. Values are checked; uncertain writes are never retried.
    # T255P requires RUN with no alarms. CAL locks the keypad, saves/restarts
    # the controller and verifies readback. Both require suitable coolant/setup.
    chiller.set_setpoint(TARGET_C)

    # 3. Read the target again. This is a target check, not a settling test.
    reported_c = chiller.read_setpoint()
    print(f"Reported setpoint: {reported_c:.2f} C")
    if reported_c != TARGET_C:
        raise RuntimeError("Target differs from the request. Inspect the equipment; do not retry.")
    print("Readback matches. Observe the actual liquid temperature separately.")

# 4. The connection is closed; the equipment keeps its current target/state.
# No automatic restoration, pump control or equipment stop is sent.
