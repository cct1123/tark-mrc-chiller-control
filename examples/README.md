# Hardware examples: scripts and standalone notebooks

Six T255P examples are available as standalone Python scripts. Five also have
standalone notebooks; the dashboard runs only as a script.
Each contains plain driver construction and connection code, with no shared
helper or factory. Set `PORT` in each example; run notebook cells top to bottom.

| Script | Notebook | Purpose |
| --- | --- | --- |
| [00_discover_connect.py](00_discover_connect.py) | [00_discover_connect.ipynb](00_discover_connect.ipynb) | List serial adapters, connect and release the configured device |
| [01_read_status.py](01_read_status.py) | [01_read_status.ipynb](01_read_status.ipynb) | Read temperature, target and diagnostics |
| [02_set_temperature.py](02_set_temperature.py) | [02_set_temperature.ipynb](02_set_temperature.ipynb) | Confirm one target change and compare readback |
| [03_record_csv.py](03_record_csv.py) | [03_record_csv.ipynb](03_record_csv.ipynb) | Record telemetry for a fixed duration to a new CSV |
| [04_dashboard.py](04_dashboard.py) | Script only | Monitor and explicitly set temperature until Ctrl+C |
| [05_connection_timing.py](05_connection_timing.py) | [05_connection_timing.ipynb](05_connection_timing.ipynb) | Time one idle connect/disconnect cycle |

## Environment

From the repository root, with Python 3.12+:

```powershell
python -m venv .venv
$chillerPython = ".\.venv\Scripts\python.exe"
& $chillerPython -m pip install -e ".[gui]"
& $chillerPython -m pip install jupyterlab ipykernel
& $chillerPython -m ipykernel install --user --name tark-chiller --display-name "Chiller (Python 3.12+)"
& $chillerPython -m jupyterlab
```

Select the **Chiller (Python 3.12+)** kernel. The GUI extra is needed only for
script 04; the notebooks need only the base package. The notebooks prefer checkout
sources when launched from the repository root or examples directory and otherwise
use the installed package. Restart the kernel after changing driver sources.

Only one process/notebook may own a device port. Relative CSV paths follow the
kernel's working directory. Physical hardware has not been tested with this
package; compare readings with the panel/reference.

## Running scripts

Set `PORT` to the identified adapter in the script, then run it, for example:

```powershell
& $chillerPython examples/00_discover_connect.py
& $chillerPython examples/01_read_status.py
```

Scripts work from any directory after package installation. They open no port
until explicitly connecting. The discovery script prints available ports even
before configuration is complete. Use Ctrl+C to interrupt a running script.
Script 04 uses a blocking localhost server without a reloader; Ctrl+C closes
acquisition and the port. Browser closure alone leaves acquisition running.
It enables two read recovery attempts for I/O failures and checksum-valid replies
with error code zero but the wrong command echo. Recovery closes/reopens the port with a fresh handshake
and preserves the 3 s reply quarantine; retry diagnostics appear in the terminal.
Other protocol faults or exhausted retries stop monitoring with a visible error.
Restart the script only after checking the reported fault and serial ownership.
Script 05 times one idle connection cycle without monitoring. Jupyter is needed
only for the notebook versions.

## T255P setup

Set the port in each script or notebook:

```python
PORT = "COM9"
```

Notebook 00 already uses your selected COM9 adapter. Other examples leave the
port unset until you identify it. The connection code explicitly supplies 9600
baud, 8N1, XON/XOFF, no hardware flow control, and deasserted RTS/DTR. Check that
these line levels suit your cable/adapter; they are editable in the connection
code if needed. Target bounds assume distilled water at **5–45 °C**, in 0.1 °C
steps. CAL-specific setup is separate in the [API guide](../docs/api.md).

**Connecting sends U, takes serial control and can change AUTO START to RUN.**
Prepare the filled cooling loop first. One-second monitoring supplies watchdog
traffic; idle calls or process stalls can allow return to local control after
more than ten seconds. The next operation sends U again.

## Execution and cleanup

Notebook 00 enumerates adapter metadata without opening ports. Configure the
identified device before running its connection section. No port or protocol is
guessed.

Each operation cell keeps connection and cleanup together. Notebook 02 pauses
for Enter before requesting one target; interrupt before confirming to cancel.
Uncertain writes are never replayed or automatically rolled back. Readback does
not establish thermal settling. Example 02 enables one confirmed target write;
script 04 also enables explicit target requests through its GUI.

Notebook 03 defaults to ten seconds of one-second polling, with a new UTC-named
file under `outputs/`. It refuses existing files, flushes rows, and raises on
poll/recording errors. Failed measurements are blank; timing does not guarantee
an exact sample count.

Script 04 runs from a terminal and serves <http://127.0.0.1:8056>:

```powershell
& $chillerPython examples/04_dashboard.py
```

**Press Ctrl+C in that terminal** to close the server,
join acquisition, close CSV and release the port. Closing the browser leaves
acquisition running. Target controls are enabled: each Apply action requests a
validated target write; monitoring and writes share the same controller lock.
The threaded HTTP server keeps refresh requests responsive during a target action.
Set `SERIAL_TRACE = True` to log every exact transmitted/received T255P frame;
rejected transactions are always logged. A device error code is reported before
an echo mismatch and is not automatically retried.
The manual permits the next request after a complete reply; there is no added
fixed inter-command delay. If a later field fails, validated temperature from
that attempt is retained as historical data while the failed session stops.

Notebook 05 measures each idle API call separately and attempts disconnect even
when connection fails. It does not start monitoring or measure active-I/O shutdown.

If cleanup fails, allow pending I/O to finish and retry
`chiller.disconnect(timeout_s=5.0)` if the controller was created. Do not rerun
setup over a live connection. Forced kernel termination cannot guarantee cleanup.
See the [hardware checklist](../docs/hardware.md#t255p-human-hardware-test) and
[cleanup details](../docs/api.md#shutdown-and-cleanup).

**Software disconnect does not stop the chiller/pump or restore its target.**
Follow the equipment shutdown procedure separately.
