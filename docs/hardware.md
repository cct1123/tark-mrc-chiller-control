# Real-hardware tutorial: prerequisites and validation steps

[Home](../README.md) · [Simulator quick start](quickstart.md) · [Python API](api.md)

**Current status: blocked before physical connection.** This release has no usable
MRC hardware mode. Do not run the numbered examples expecting them to control a
chiller: examples 01–05 create a simulator; example 06 only shows configuration.
The steps below describe the sequence for a
future documented, reviewed hardware setup and what evidence each step must produce.

## 1. Hardware connection

Record the chiller model/suffix, serial number, controller model and firmware.
Obtain the communication manual for that controller and confirm that it applies
to the actual unit. Have the lab approve the coolant limits and test setup before
physical work. Do not choose a cable or adapter from connector shape alone.

![PC, adapter and chiller connection plan with a missing protocol gate](assets/hardware-setup.svg)

Verified source: Tark's public [MRC150/300 User Manual, Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf).
The original attachment was unavailable; compare its revision if supplied later.

- Page 12 mentions RS-232/RS-485 and delegates details to a separate controller manual.
- Page 4 removes the DH4 RS-485 option; page 7 lists DH2 RS-232. Confirm the unit's interface.
- Pages 6 and 11 require coolant, airflow clearance and physical installation/leak checks.
- Page 7 gives 2–40 °C for distilled water. This is the software default, not approval for a particular setup.

Follow the unit's installation manual for plumbing and power. This drawing is a
connection plan, not a pinout. No serial cable wiring, supply, baud rate or device
address is specified by this software.

## 2. Identify the serial port

After the interface and adapter are verified, identify the adapter in the operating
system. On Windows, use Device Manager's **Ports (COM & LPT)** list and record the
adapter identity and COM port. A COM number identifies an operating-system port;
it does not prove that the correct chiller is attached or that commands are safe.
This project has not performed device discovery or a port-open test.

Before any software connection, the protocol implementation needs all of:

| Required information | Current state |
| --- | --- |
| Baud rate, parity, data bits, stop bits and flow control | Unknown |
| RS-485 address/direction settings, if applicable | Unknown |
| Command syntax/registers, frame boundaries and terminators | Unknown |
| Temperature read, target read and target write | Unknown |
| Acknowledgements, errors, checksums/CRC and response matching | Unknown |
| Units/scaling, timeouts and side effects of reads/startup | Unknown |

These details belong in the protocol layer and explicit serial settings. The
existing `MissingProtocol` refuses connection before a port opens. Neither a
front-panel menu nor the test-only JSON messages define a usable wire protocol.

### Software configuration template

Run this now to confirm the deliberate protocol block; it opens no port and exits
with code 1 and **Hardware unavailable: Controller communication manual is required**:

```powershell
.\.venv\Scripts\python examples/06_hardware_configuration.py
```

The template's `configure(settings, rs485_mode=None)` returns a disconnected
`Chiller`. Its `connect()` also fails before serial endpoint creation. To prepare
the later configuration, obtain values for every field below; do not copy the
arbitrary settings in software tests.

| API configuration | Required fields and source |
| --- | --- |
| `SerialSettings` for either interface | `port` from the identified OS adapter; `baudrate`, `bytesize`, `parity`, `stopbits`, `xonxoff`, `rtscts`, `dsrdtr`, `rts`, `dtr` from the applicable controller/adapter documentation |
| RS-232 | Use `RS232Transport(settings)`; the template selects it when no RS485Mode is supplied |
| RS-485 | Use `RS485Transport(settings, mode=...)`; `RS485Mode` needs explicit `rts_level_for_tx`, `rts_level_for_rx`, `loopback`, `delay_before_tx`, `delay_before_rx` |
| Protocol codec | Implement documented commands, addressing, response checks, units and errors in `src/tark_chiller/protocol.py`; configure `SerialDevice` with that codec after software review |

Native RS-485 direction control depends on the OS and adapter; it has only been
tested with fakes here. The transport's default 1 s transaction budget and 4096-byte
response cap are software limits, not manufacturer settings. Opening a real port
can change RTS/DTR levels even before a request; review that behavior with the adapter.

Once the protocol exists, install serial support using
`.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"`.
This installation alone does not authorize or enable physical operation.

## 3. Read-only validation

**Blocked until protocol support and a physical test plan are approved.** Use the
least consequential documented read first; even read commands can have side
effects. The future approved script must follow these stages without setpoint writes:

1. **Interface:** confirm the recorded adapter/port and reviewed serial values,
   then call `connect()`. Call `get_status()` only as implemented and documented;
   a local connection status must not be reported as device identification.
2. **Temperature:** repeatedly call `get_temperature()`. Record UTC time, software
   value and front-panel value with matching units; compare against the approved
   reference. Exercise approved disconnection/timeout handling and explicit reconnect.
3. **Setpoint:** call `get_setpoint()` without changing it. Record and compare it
   with the front panel. Preserve this original target for the later write stage.

Record exact request/reply bytes, configuration, reference instrument and observed
errors in the engineering evidence. Do not use the manual's general accuracy figure
as a measured result. Stop on unexpected replies; do not discover commands by trial.

## 4. Logging

Once reads are validated, use `Monitor` and `CsvLogger` as in
[example 03](../examples/03_log_temperature.py), with the reviewed physical Chiller
instance replacing the simulator. Check the backend label, units, UTC timestamps
and missing-value/error rows. Compare CSV values with the approved read-only results.
Do not use a `simulator` or `fake-serial` CSV as a hardware measurement.

## 5. Safe setpoint change

**The controlled-write stage requires explicit authorization for device writes**, after the read-only
stages pass. Record the installed coolant/profile and permitted bounds. Choose one
small target change within the already-safe operating region, never an extreme
limit test. Call `set_setpoint(value_c)` once, then read `get_setpoint()` and observe
temperature independently. Restore the original target only if appropriate and
within the approved scope; record both results. If confirmation is lost,
resolve the uncertainty through documented readback; do not resend automatically.
Controller acceptance cannot prove that a temperature is safe for the coolant.

## 6. GUI use

After API and logging validation, the same dashboard can display the approved
Chiller/Monitor through `create_app(chiller, monitor.state)`. Keep acquisition
outside Dash. Confirm the physical backend label, sample age, fault reporting,
recording and target readback before relying on the display.

The supplied CLI and [example 05](../examples/05_launch_dashboard.py) launch only
the simulator. There is no hardware selector or hidden automatic port detection.

## 7. Shutdown

Stop new polls, disconnect software, wait for polling to finish, then close the
CSV. Confirm that the file is complete and the software port is released. Follow
the lab's approved equipment shutdown procedure separately: `disconnect()` does
not switch off the chiller, pump or cooling. No serial stop command is documented.

**Needed next:** the matching controller communication manual and unit identity.
Extract every setting/command in the table above with manual page references;
implement the codec and source-derived byte fixtures, then rerun:

```powershell
.\.venv\Scripts\python -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python -m ruff check src tests examples development
.\.venv\Scripts\python -m ruff format --check src tests examples development
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\python -m build --no-isolation
```

These are software checks; first install the [development dependencies](../development/README.md).
Record the candidate revision, complete configuration and exact first read
transactions for review before running any physical script. The final executable
hardware command depends on that reviewed configuration; none can be specified
honestly today. [Development status](../STATE.md) lists unresolved source differences
for the actual unit. Software work and simulator operation are complete independently.
