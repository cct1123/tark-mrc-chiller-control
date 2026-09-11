# Real hardware: preparation and validation

[Home](../README.md) · [Simulator quick start](quickstart.md) · [API](api.md)

**Hardware control remains unavailable.** The controller communication manual is
missing; no physical chiller has been validated. The following sequence prepares a
future documented, reviewed setup. Examples 01–05 use the simulator. Example 06
is a configuration template that refuses connection without a codec.

## 1. Confirm the hardware and connection

Record the chiller model/suffix, controller model, firmware and installed coolant.
Obtain the communication manual matching that controller. Confirm the interface,
adapter and wiring before choosing a cable; connector shape is not a pinout.
Use the unit's installation manual and lab procedure for plumbing and power.

Verified source: Tark's public [MRC150/300 User Manual, Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf).
The originally mentioned attachment was unavailable; compare its revision if supplied.

- Page 12 mentions RS-232/RS-485 and delegates communication details to a separate controller manual.
- Page 4 removes the DH4 RS-485 option; page 7 lists DH2 RS-232. Check the actual unit.
- Page 7 lists a distilled-water range of 2–40 °C. That software default is not approval for every physical setup.

The software has no verified serial signals for coolant presence, leaks, flow or
fluid level. Those conditions require the lab's physical checks.

![Documented computer, adapter and chiller connection plan](assets/hardware-setup.svg)

## 2. Identify the serial port and settings

For a future approved setup, identify the adapter in the operating system.
On Windows, Device Manager's **Ports (COM & LPT)** list shows COM names.
A COM name does not establish device identity or safe commands. No discovery or
physical port-open test has been performed in this project.

Obtain every required value from the controller/adapter documentation:

| Information | Required detail |
| --- | --- |
| Serial format | Baud rate, parity, data bits, stop bits, flow control |
| Adapter lines | RTS/DTR levels; RS-485 address/direction settings if applicable |
| Wire protocol | Commands/registers, framing and terminators |
| Reads and write | Temperature read, setpoint read, setpoint write |
| Reply checks | Acknowledgements, errors, response identity, checksum/CRC if used |
| Interpretation | Units/scaling, timing limits and read/startup side effects |

The generic backend is `tark_chiller.serial.SerialDevice`.
Its `SerialSettings` has ten explicit fields:
`port`, `baudrate`, `bytesize`, `parity`, `stopbits`,
`xonxoff`, `rtscts`, `dsrdtr`, `rts`, `dtr`.
An optional `RS485Mode` supplies `rts_level_for_tx`, `rts_level_for_rx`,
`loopback`, `delay_before_tx` and `delay_before_rx`.
SerialDevice configuration is read-only after construction, including its timeout
and response-size limit. Close it and create a new backend to change settings.

[Example 06](../examples/06_hardware_configuration.py) accepts those documented
objects and returns a disconnected Chiller. It supplies no guessed values or
codec. Running it directly reports the dependency and exits without opening a port:

```powershell
.\.venv\Scripts\python examples/06_hardware_configuration.py
```

Expected: **Hardware unavailable: Controller communication manual is required**,
exit code 1. This intentional failure is not a hardware test.

The serial device's 1 s timeout and 4096-byte response limit are software budgets,
not Tark settings. Native RS-485 support depends on the OS/adapter. Opening a
physical port can affect RTS/DTR and requires review even before transmission.
Installing the serial extra supplies pySerial only:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
```

## 3. Validate reads first

**Blocked until a documented codec and physical test plan are approved.**
Implement documented commands behind the `Codec` interface in `serial.py`,
preserving the public Chiller API. Test exact source-derived request/reply bytes
with memory endpoints before physical work.

For the reviewed physical candidate:

1. Confirm the recorded unit, port and settings, then call `connect()`.
2. Use `read_status()` only with documented semantics. Connection status alone is not device identification.
3. Compare repeated `read_temperature()` values with the front panel and approved reference, using matching units.
4. Read `read_setpoint()` without changing it and record the original target.

Record exact settings, replies and comparisons. Even nominal reads can have side
effects. Stop on unexpected replies; do not discover commands by trial.

## 4. Record validated readings

Once reads pass, call `chiller.start_monitoring(csv_path="hardware-run.csv")`
on the reviewed physical Chiller. Inspect its snapshot and CSV for hardware
labels, UTC timestamps, units and blank/error rows. Check both `service_error`
and `logging_error`. Do not label simulator or fake-serial data as physical results.

## 5. Make one safe target change

After read validation and authorization for writes, choose a small change within
the approved coolant/setup limits. Call `set_setpoint(value_c)` once, read
`read_setpoint()`, and observe temperature independently. Restore the original
target only within the approved scope.

If confirmation is lost, resolve the uncertainty by documented readback.
Never resend automatically. Accepted controller values do not prove coolant safety.

## 6. Use the optional GUI

After API and recording validation, pass the approved Chiller and monitoring
handle to `create_app(chiller, monitor)`. The host owns connection and acquisition.
Check backend, target, sample age, errors and recording status before relying on
the display. The supplied CLI/dashboard example always creates a simulator;
there is no hardware selector.

## 7. Shut down

Call `chiller.disconnect()` or leave its context manager. The driver cancels
recovery, stops monitoring and closes CSV. A timeout means cleanup is incomplete;
resolve it before treating the recording or port as closed.

Follow the approved equipment shutdown procedure separately. Disconnecting
software does not switch off the physical chiller, pump or cooling. No serial
stop command is documented.

**Next dependency:** the matching controller communication manual and unit identity.
Only after protocol implementation, software tests and candidate review can
physical validation begin. [Development status](../STATE.md).
