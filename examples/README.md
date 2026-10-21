# Hardware tutorials

Four plain scripts introduce the CAL/T255P hardware API. Read them top to bottom:
numbered comments explain each step, and constants at the top are yours to edit.
There are no command menus, `main()` wrappers or simulator selections.
[`hardware.py`](hardware.py) holds the shared setup; it opens nothing on import.

## Set up once

From the repository root, with Python 3.12+:

```powershell
python -m venv .venv
$chillerPython = ".\.venv\Scripts\python"
& $chillerPython -m pip install ".[gui]"
```

The `gui` extra is needed only for tutorial 04. The other tutorials need only
`python -m pip install .`. Run one script at a time and close other programs using
the port. The scripts work from any directory; relative CSV paths follow your
current working directory. Commands below assume the repository root.

Edit the constants in [`hardware.py`](hardware.py) for the actual unit. No port
is guessed: incomplete setup raises an error before a serial endpoint is created.
Consult the [hardware setup and first-use guide](../docs/hardware.md) for cable,
line levels, coolant, initial checks and recovery. Physical hardware has not been
tested with this package; verify readings against the panel/reference first.

For a first T255P bench session, follow the
[T255P hardware checklist](../docs/hardware.md#t255p-human-hardware-test):
read-only checks first, then one setpoint change, response recording and cleanup.

### T255P setup

Select `DEVICE_MODEL = "t255p"` and replace `SERIAL_SETTINGS = None` with this
configuration, using the actual port and verified RTS/DTR levels instead of `...`:

```python
SERIAL_SETTINGS = {
    "port": ...,
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "xonxoff": True,
    "rtscts": False,
    "dsrdtr": False,
    "rts": ...,
    "dtr": ...,
}
```

Leave CAL address/identity fields and `RS485_MODE` as `None`. The helper uses
distilled-water bounds of **5–45 °C**; writes require 0.1 °C steps. Signed reads
are supported, but sub-zero writes are not.

**Connecting sends U, takes serial control and can change AUTO START to RUN**,
even in the read-only tutorials. Prepare the physical loop before connecting.
Normal one-second monitoring supplies watchdog traffic. Idle calls, stopping
monitoring or process stalls can let the unit return to local control after more
than ten seconds; the next API operation sends U again.

### CAL setup

Select `DEVICE_MODEL = "cal"`, enter the actual `CAL_ADDRESS` (1–247), and replace
`SERIAL_SETTINGS = None` with verified settings:

```python
SERIAL_SETTINGS = {
    "port": ...,
    "baudrate": ...,
    "bytesize": 8,
    "parity": ...,
    "stopbits": 1,
    "xonxoff": False,
    "rtscts": False,
    "dsrdtr": False,
    "rts": ...,
    "dtr": ...,
}
```

Match the actual baud rate and parity to the unit's menu. The driver requires a
supported CAL 3300/9300, RTD input and Celsius. Its default distilled-water bounds
are **2–40 °C**. Keep expected identity fields as `None` for tutorial 01; then copy
the actual printed model/firmware hex codes into them before tutorial 02.
Use `RS485_MODE` only when your verified adapter requires native direction control.

## 1. Connect and read

[`01_read_status.py`](01_read_status.py) constructs the driver, connects through its
context, and reads temperature, target and a status dictionary. Context exit disconnects,
including after a read error; a cleanup failure is attached to the original error.
CAL diagnostics include actual model/firmware codes; T255P diagnostics report
mode and alarms but do not identify the unit. Readings are sequential, not
simultaneous, and temperatures are Celsius.

```powershell
& $chillerPython examples/01_read_status.py
```

## 2. Set one target

[`02_set_temperature.py`](02_set_temperature.py) explicitly enables target writes.
Edit `TARGET_C` for your experiment. It prints the existing target and pauses for
Enter before sending the chosen target once; Ctrl+C cancels the request. It then
reads the target back. CAL also needs the actual expected identity codes in
`hardware.py` and uses a lock → stage → save/restart sequence. T255P requires RUN
with no reported alarms. Successful readback does not establish thermal settling.

```powershell
& $chillerPython examples/02_set_temperature.py
```

Run/standby, pump-only, display-unit and clock methods are not exposed by this
package.
Inspect the equipment after any uncertain write; no failed write is retried or
automatically rolled back. Only tutorial 02 enables writes.

## 3. Record CSV

[`03_record_csv.py`](03_record_csv.py) starts one monitor, records for `DURATION_S`
(default ten seconds), stops it, and prints the sample/row counts. Edit
`INTERVAL_S` for the poll interval; serial query time can extend it. This is a
duration-based run, not an exact row count.

```powershell
& $chillerPython examples/03_record_csv.py
```

Each run creates a UTC-named file under `outputs/`. Rows contain UTC/elapsed time,
Celsius readings, connection/status and errors; failed readings are blank.
Existing files are refused and rows are flushed. A poll or recording error ends
the script. The controller context joins the worker and closes CSV on Ctrl+C or
an error, preserving the original error if cleanup also fails. See
[cleanup and retry](../docs/api.md#shutdown-and-cleanup) if resources remain owned.

## 4. Watch the dashboard

[`04_dashboard.py`](04_dashboard.py) uses one controller for hardware, monitoring and recording
with the Dash app at <http://127.0.0.1:8050>. Both driver and dashboard disable
target writes. Set `CSV_PATH` to a new filename if you also want recording; leave
it `None` to watch without saving. The GUI shows sample age, failures and logging
errors. The reloader is disabled to keep one serial owner.

```powershell
& $chillerPython examples/04_dashboard.py
```

Browser refresh or closure leaves acquisition running. Ctrl+C in the terminal
stops the app, joins the worker, closes CSV and releases the port.
**Closing a connection or cancelling a script does not stop the chiller/pump or
restore its original target.** Follow the equipment shutdown procedure separately.
