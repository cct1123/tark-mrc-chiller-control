"""05 · Request one T255P RUN/STANDBY change using the documented serial command.

Standalone serial example; set_mode() handles the requested RUN/STANDBY state.
Run one device owner at a time. See the matching notebook for operating notes.
"""

from time import perf_counter, sleep

import serial

from tark_chiller.protocols import T255P, ProtocolError

# 2. Choose the identified port and exactly one requested mode.
PORT = None  # Set the identified serial port, e.g. "COM9".
MODE = "STANDBY"  # "RUN" starts circulation and temperature control together.


# 3. Bounded, single-attempt transactions; reuse the production reply validator.
def exchange(connection, command, data=b""):
    """Send once; validate framing, checksum, command echo and device error code."""
    body = b"." + command + data
    request = body + f"{sum(body) & 0xFF:02X}\r".encode("ascii")
    connection.reset_input_buffer()
    deadline = perf_counter() + 3.0
    try:
        print(f"TX {request!r}")
        if connection.write(request) != len(request):
            raise ProtocolError("Partial serial write; outcome unknown")
        response = bytearray()
        while perf_counter() < deadline:
            response.extend(connection.read(1))
            if not response:
                continue
            if T255P.is_complete(bytes(response)):
                if perf_counter() >= deadline:
                    raise TimeoutError("Reply arrived outside the 3 s response budget")
                print(f"RX {bytes(response)!r}")
                result = T255P.reply(command, bytes(response))
                if connection.in_waiting:
                    raise ProtocolError("Unexpected trailing response bytes")
                return result
            if len(response) >= 64:
                raise ProtocolError("Response exceeded 64-byte budget")
        raise TimeoutError("No complete reply within 3 s")
    except BaseException:
        # Do not permit immediate reruns to overlap an incomplete/invalid reply.
        sleep(max(0.0, deadline - perf_counter()))
        raise


# 4. Confirm before opening: even U can change AUTO START to RUN.
if not isinstance(PORT, str) or not PORT.strip():
    raise ValueError('Hardware not configured: set PORT, e.g. "COM9". No port was opened.')
if MODE not in ("RUN", "STANDBY"):
    raise ValueError('MODE must be exactly "RUN" or "STANDBY". No port was opened.')

print("Prepare the filled cooling loop. Stop other notebooks/dashboard owners first.")
print("Connection sends U and can start AUTO START, even when requesting STANDBY.")
print("RUN starts pump and temperature control; STANDBY stops both.")
if input(f"Type {MODE} to request it on {PORT}; anything else cancels: ") != MODE:
    raise ValueError("Cancelled. No port was opened.")

# Configure while closed, including RTS/DTR; do not reconfigure a live UART.
connection = serial.Serial(
    port=None,
    baudrate=9600,
    bytesize=8,
    parity="N",
    stopbits=1,
    xonxoff=True,
    rtscts=False,
    dsrdtr=False,
    timeout=0.05,
    write_timeout=3.0,
)
mode_write_started = False
try:
    connection.port = PORT
    connection.rts = False
    connection.dtr = False
    connection.open()
    protocol = T255P(allow_writes=True)

    def io(command, data):
        global mode_write_started
        if command == b"G":
            mode_write_started = True  # Track actual write attempts, including partial writes.
        return exchange(connection, command, data)

    before = protocol.get_status(io)
    alarms = protocol.get_alarms(io)
    print(f"Before: mode={before['mode']}; chiller on={before['chiller_on']}")
    print(f"Alarm summary: {before['summary_alarm']}; alarms={alarms}")
    # Alarm flags are diagnostics; the device determines whether the mode can apply.

    # The setter owns the current-state check, conditional G and readback verification.
    after = protocol.set_mode(MODE, io)
    print(f"After: mode={after['mode']}; chiller on={after['chiller_on']}")
    if not after["mode_command_sent"]:
        print(
            f"{MODE} request already satisfied; reported mode={after['mode']}, "
            f"chiller on={after['chiller_on']}. No G command sent."
        )
    else:
        print(f"Reported {MODE} matches. Check the physical pump/flow and panel separately.")
except BaseException as error:
    if mode_write_started:
        error.add_note(
            "Mode outcome may be uncertain. Inspect the panel; do not replay the request."
        )
    try:
        connection.close()
    except BaseException as cleanup:
        error.add_note(f"Serial cleanup failed: {cleanup}")
    raise
else:
    connection.close()

# 5. No retry, automatic rollback, target change or mode change during cleanup.
print("Port closed. Equipment retains its mode; disconnect is not a stop command.")
