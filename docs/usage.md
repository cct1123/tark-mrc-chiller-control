# Using the controller

[Home](../README.md) · [Quick start](quickstart.md) · [Troubleshooting](troubleshooting.md)

## Simulator and real hardware

The app always starts in **simulator mode**, at 20 °C with a 20 °C target. The
temperature changes gradually toward the target. This model demonstrates the
software; it does not predict the performance of a physical MRC chiller.

Restarting the app resets the simulator. Within a running app, **Disconnect**
pauses the simulation and **Connect / retry** resumes it with the same target.

![Simulator and hardware paths: both use Chiller; simulator runs now, physical communication stops at the missing protocol](assets/modes.svg)

**Real-hardware mode is not available.** The project has serial connection code
for RS-232/RS-485, but the controller's commands and settings are still unknown.
The current `SerialDevice` uses `MissingProtocol`, which blocks connection before
opening a port. There is no command-line option to select a physical chiller.
Installing pySerial does not add the missing protocol.

Fake serial tests replace the serial device with software. Their messages and
settings are **test data only**, not instructions for connecting an MRC chiller.

## Dashboard controls and status

Launch it using the [quick-start command](quickstart.md#2-start-the-dashboard-and-recording).
A **poll** is one attempt to read the device. Each poll produces a **sample**:
readings and a timestamp, or an error if the attempt fails.

| Display or control | Meaning |
| --- | --- |
| Last poll / Sample age | Result of the latest poll and seconds since it started. **Stale** means a new sample is overdue. These values do not prove the device is still connected now. |
| Temperature / Setpoint | Reported °C values; an unavailable poll shows dashes and a gap in the plot. |
| Samples / Failed polls | Total polls and failed polls since this application started. |
| CSV Enabled / Rows written | Recording is enabled; the count shows rows written so far, including error rows. **Off** means recording was not enabled. **Failed** means a file error stopped recording. |
| Apply setpoint | Checks the Celsius value against the limits and sends one request. Check the Setpoint display on the next update to confirm the reported target. |
| Disconnect | Closes the software connection. The app keeps polling and records unavailable rows until you reconnect or stop it. |
| Connect / retry | Opens the software connection. It does not restart monitoring if monitoring has stopped. |

The plot keeps the latest 3,600 samples by default; the CSV keeps all rows written
during recording. Reloading or closing the page does not stop monitoring. Use
Ctrl+C in the launching terminal to stop the app. Closing a software connection
does not mean that a physical chiller has been switched off.

![Data flow: independent polling reads the API, records CSV and publishes shared history; GUI refresh reads history and explicit controls use the API](assets/data-flow.svg)

## CSV recording

Use a new filename, or choose `--append-csv` to add readings to an existing file.
Before appending, the app checks the column names and every row. It refuses
damaged or incomplete files without changing them. Only one recording process
can write to a file at a time. Each row is made available to file readers after
writing, but data may still be lost during a power failure.

| Column | Meaning |
| --- | --- |
| session_id | ID for this recording session. A new run gets a new ID, even when adding to an existing file. |
| timestamp_utc | Date and time when the poll started, in UTC (not the computer's local time zone). |
| elapsed_s | Seconds since this monitor was created; resets for a new monitor. |
| temperature_c / setpoint_c | Celsius readings; blank for a failed poll, never a placeholder zero. |
| backend / connected | Device type (for example, simulator) and whether the poll succeeded. |
| status_detail / error | Status or error message. The error field is blank when the poll succeeds. |

If a file error occurs, recording stops and the app displays the error. Monitoring
continues. Fix the storage problem, stop the app and start a new recording. Keep
any damaged file for investigation. CSV files contain no flow, coolant, level or
leak measurements.

## Basic Python API

After installing, save this as `read_temperature.py` in the project folder.
Run it using the environment created in the quick start.

```python
from tark_chiller import Chiller, SimulatedDevice

chiller = Chiller(SimulatedDevice())
try:
    chiller.connect()
    print("Connected:", chiller.is_connected)
    chiller.set_setpoint(18.0)
    print("Temperature (Celsius):", chiller.get_temperature())
    print("Setpoint (Celsius):", chiller.get_setpoint())
    print("Status:", chiller.get_status())
finally:
    chiller.disconnect()
```

```powershell
.\.venv\Scripts\python read_temperature.py
```

Temperature initially remains near 20 °C; setting a target does not change it
instantly. These seven operations form the common API for the simulator and
serial device. Call `connect()` before reading or setting temperatures. You can
check connection status while disconnected. Device errors use `ChillerError`;
invalid setpoints raise `SetpointValidationError`.

## Python sampling and CSV

Save as `record_temperature.py`. This example takes ten samples, one at a time,
using the same `Monitor` and `CsvLogger` classes as the app.

```python
from time import sleep

from tark_chiller import Chiller, SimulatedDevice
from tark_chiller.csvlog import CsvLogger
from tark_chiller.monitoring import LiveState, Monitor

chiller = Chiller(SimulatedDevice())
state = LiveState(capacity=120)
with CsvLogger("outputs/python-session.csv") as logger:
    monitor = Monitor(chiller, state, logger=logger)
    try:
        chiller.connect()
        chiller.set_setpoint(18.0)
        for _ in range(10):
            sample = monitor.poll_once()
            print(sample.temperature_c, sample.error)
            sleep(0.5)
    finally:
        chiller.disconnect()
    print("Rows written:", logger.rows_written)
```

```powershell
.\.venv\Scripts\python record_temperature.py
```

Expect ten CSV rows and gradual cooling. Use a fresh filename on rerun, or
`CsvLogger("outputs/python-session.csv", append=True)` to validate and append.
The GUI app runs monitoring in a background thread. Do not call `poll_once()`
yourself while that thread runs. Share one `Chiller` instance per device. See the
[ownership and shutdown rules](../ARCHITECTURE.md#safety-and-lifecycle) for custom applications.

## Safety and hardware limits

Every setpoint is checked before it reaches the device. The default limits for
distilled water are **2–40 °C**, including both limits. Python API calls must use
numbers, not text. Strings, booleans, NaN (not a number), infinity and values
outside the limits are rejected. All numeric values mean Celsius: the app does
not convert Fahrenheit or Kelvin.

Other coolants require a Python `CoolantProfile` with a name, finite temperature
limits and a source explaining those limits. Set the profile at both the API and
device. There is no command-line option to change it. A profile cannot identify
the liquid installed or prove that operation below 0 °C is safe. A controller
accepting a value does not establish that the value is safe.

The software never automatically repeats a setpoint write, including after a
reconnect. If the device's confirmation is lost, the write may have succeeded;
read the setpoint before deciding whether to send another request.

The hardware facts below come from Tark's public
[MRC150/300 User Manual, Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf),
used in place of the unavailable original attachment:

- Page 7 specifies a 2–40 °C distilled-water control range. This supports the
  software default; it does not establish the actual unit's setup or coolant.
- Page 12 describes RS232/RS485 and a separate controller-manufacturer manual.
  Page 4 records removal of the DH4 RS485 option; page 7 lists DH2 RS232.
  Confirm the interface of the particular unit rather than assume both exist.
- Pages 11–13 describe physical coolant, flow and leak checks and indicators.
  They do not say that the software can read these signals through the serial port.

**Before testing a physical chiller:** identify its controller model and firmware,
obtain the matching communication manual, and implement and test its protocol.
Then confirm the unit's interface, wiring, coolant limits and test setup before
requesting approval for physical testing. This guide does not cover hardware setup.
Source details and open questions are recorded in
[STATE.md](../STATE.md#external-dependencies-and-precise-next-action).
