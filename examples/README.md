# Hardware examples: scripts and standalone notebooks

Seven T255P examples are available as standalone Python scripts. Six also have
standalone notebooks; the dashboard runs only as a script.
Each contains explicit connection code, with no shared tutorial helper or factory.
Example 05 uses pyserial directly and reuses the production reply validator; the
others construct `TarkChiller`. Review `PORT` and operation options in each
example; run notebook cells top to bottom.

For a first development task without equipment, start with the
[memory-only simulator exercise](../docs/development.md#first-exercise-read-a-simulated-temperature).
These hardware examples are a separate workflow for a prepared, identified unit.

| Script | Notebook | Purpose |
| --- | --- | --- |
| [00_discover_connect.py](00_discover_connect.py) | [00_discover_connect.ipynb](00_discover_connect.ipynb) | List serial adapters, connect and release the configured device |
| [01_read_status.py](01_read_status.py) | [01_read_status.ipynb](01_read_status.ipynb) | Read temperature, target and diagnostics |
| [02_set_temperature.py](02_set_temperature.py) | [02_set_temperature.ipynb](02_set_temperature.ipynb) | Confirm one target change and compare readback |
| [03_record_csv.py](03_record_csv.py) | [03_record_csv.ipynb](03_record_csv.ipynb) | Record telemetry for a fixed duration to a new CSV |
| [04_connection_timing.py](04_connection_timing.py) | [04_connection_timing.ipynb](04_connection_timing.ipynb) | Time one idle connect/disconnect cycle |
| [05_run_standby.py](05_run_standby.py) | [05_run_standby.ipynb](05_run_standby.ipynb) | Confirm one RUN/STANDBY request and verify mode readback |
| [06_dashboard.py](06_dashboard.py) | Script only | Connect, monitor, change target/mode and shut down from buttons |

## Environment

From the repository root, with [uv](https://docs.astral.sh/uv/) installed:

```powershell
uv sync --locked --extra gui --group notebooks
uv run --locked --extra gui --group notebooks python -m ipykernel install --user --name tark-chiller --display-name "Chiller (Python 3.12)"
uv run --locked --extra gui --group notebooks jupyter lab
```

Select the **Chiller (Python 3.12)** kernel. uv manages `.venv` and installs the
checkout in editable mode. The `notebooks` group is optional; omit it for scripts.
The GUI extra is needed only for script 06; the notebooks need only the base
package. The notebooks prefer checkout
sources when launched from the repository root or examples directory and otherwise
use the installed package. Restart the kernel after changing driver sources.

Only one process/notebook may own a device port. Relative CSV paths follow the
kernel's working directory. Limited T255P bench records cover read-only monitoring,
and the operator reported a successful standalone mode-notebook run. API/dashboard
mode controls and CAL hardware remain unverified on equipment; compare readings
with the panel/reference. See [validation scope](../docs/protocol.md#bench-evidence-and-limits).

## Running scripts

Set `PORT` to the identified adapter in the script, then run it, for example:

```powershell
uv run --locked --extra gui python examples/00_discover_connect.py
uv run --locked --extra gui python examples/01_read_status.py
```

Scripts work from any directory after package installation. They open no port
until explicitly connecting. The discovery script prints available ports even
before configuration is complete. Use Ctrl+C to interrupt a running script.
Script 06 uses a blocking localhost server without a reloader; Ctrl+C closes
acquisition and the port. Browser closure alone leaves acquisition running.
It enables two read recovery attempts for I/O failures, checksum-valid communication
errors 1/2 (checksum/bad command), and error-zero replies with the wrong command echo.
Recovery closes/reopens the port with a fresh handshake
and preserves the 3 s reply quarantine; retry diagnostics appear in the terminal.
An exhausted read retry budget produces a failed poll; monitoring and CSV remain
active and try a fresh connection on the next interval. Other protocol faults
stop monitoring with a visible error. Check the fault and finish cleanup before
reconnecting after a fatal error.
Script 04 times one idle connection cycle without monitoring. Jupyter is needed
only for the notebook versions.

## T255P setup

Set the port in each script or notebook:

```python
PORT = "COM9"
```

The notebooks and dashboard script currently contain `PORT = "COM9"`; scripts
00–05 leave the port unset. These are editable local choices, not discovery
results. Notebook 05 currently selects `MODE = "RUN"`, while its script defaults
to `"STANDBY"`. Review both values before execution. The connection code supplies 9600
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
script 06 also enables explicit target and mode requests through its GUI.

Notebook 03 defaults to ten seconds of one-second polling, with a new UTC-named
file under `outputs/`. It refuses existing files, flushes rows, and raises on
poll/recording errors. Unavailable fields are blank; a failed T255P poll can retain
a validated temperature from its final attempt as historical data. Timing does not guarantee
an exact sample count.

Notebook 04 measures each idle API call separately and attempts disconnect even
when connection fails. It does not start monitoring or measure active-I/O shutdown.

Example 05 is a standalone serial mode-control example. The controller API now
also provides `run()`/`standby()` and the dashboard exposes both controls.
Treat this as an advanced direct-serial diagnostic: it retains the reviewed
U/J/G/U exchange and explicit cleanup. Using a controller context and then its
mode method adds a connection U exchange; the two examples have different
connection sequences even though both verify the requested equipment mode.
Set `PORT` and `MODE` (`"RUN"`
or `"STANDBY"`), then type that exact mode when prompted, before the port opens:

```powershell
uv run --locked python examples/05_run_standby.py
```

RUN starts the pump and temperature regulation together using the existing target;
STANDBY stops both. The initial U exchange can start AUTO START even when requesting
STANDBY, so prepare the filled loop first. The example sends one documented G
request, checks its echo and U mode/chiller-on readback, and closes without retry
or rollback. RUN is refused with reported alarms or SAFETY DEFAULT; STANDBY is
available with alarms. Check the physical panel/flow separately. The operator
reported successfully running notebook 05 on hardware on 2026-10-21; the new
API/dashboard integration has separate software validation. An uncertain result requires inspection before
another request. See the [Rev C manual](https://neurophysics.ucsd.edu/Manuals/ThermoTek/T255P_chiller_manual.pdf),
printed pp7–8 and 23–29.

Script 06 runs from a terminal and serves <http://127.0.0.1:8056>:

```powershell
uv run --locked --extra gui python examples/06_dashboard.py
```

The page opens disconnected. Press **Connect**, then **Start monitoring**.
Adjust the polling interval and optional new CSV path before starting a run.
The same button switches to **Stop monitoring**, which closes CSV while leaving
the connection open; restarting resets history and requires a new CSV path when recording.
**Shutdown GUI** closes the server, joins acquisition, closes CSV and releases
the port. Ctrl+C in the terminal also cleans up. Shutdown does not send standby
to the chiller or pump. Closing the browser leaves acquisition running.
Target and RUN/STANDBY controls are enabled: each Apply action requests a
validated target write; monitoring and writes share the same controller lock.
The threaded HTTP server keeps refresh requests responsive during a target action.
Set `SERIAL_TRACE = True` to log every exact transmitted/received T255P frame;
rejected reply validations are logged at ERROR level. A device error code is
reported before an echo mismatch. Error codes 1/2 may trigger read recovery;
target and mode writes are never retried. The console logs are not saved to a
trace file unless you configure a file handler.
The manual permits the next request after a complete reply; there is no added
fixed inter-command delay. If a later field fails, validated temperature from
the final attempt can be retained as historical data. Retryable read failures
keep monitoring active; fatal protocol faults stop the worker and close CSV.

If cleanup fails, allow pending I/O to finish and retry
`chiller.disconnect(timeout_s=5.0)` if the controller was created. Do not rerun
setup over a live connection. Forced kernel termination cannot guarantee cleanup.
See the [hardware checklist](../docs/hardware.md#t255p-human-hardware-test) and
[cleanup details](../docs/api.md#shutdown-and-cleanup).

**Software disconnect does not stop the chiller/pump or restore its target.**
Follow the equipment shutdown procedure separately.
