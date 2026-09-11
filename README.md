# Tark MRC150/300 laboratory controller

Watch temperature, set a safe target, and record an experiment from Python or a
Plotly Dash dashboard. The simulator runs without a chiller or serial adapter.

**Physical MRC control is not available yet.** The matching controller communication
manual is missing. The serial code is tested with software devices; no physical
chiller has been validated. [Hardware setup and current limits](docs/hardware.md).

![Simulator dashboard with temperature, setpoint, connection and recording status](docs/assets/dashboard.jpg)

*Actual simulator session. [Annotated dashboard tour](docs/usage.md#dashboard-tour).*

## Start in a few minutes

Use Python **3.12 or newer**. In Windows PowerShell, open the downloaded project
folder containing `pyproject.toml`. Check `python --version` before installing.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv
```

Open **[127.0.0.1:8050](http://127.0.0.1:8050)** in your browser. Enter **18** as the
setpoint (target temperature). Watch the simulated temperature fall from 20 °C.
Press **Ctrl+C in the terminal** to stop. Use a new CSV filename on the next run.

[Step-by-step quick start](docs/quickstart.md) · [Troubleshooting](docs/troubleshooting.md)

## What it does

- Reads temperature, setpoint and device status through one small Python API.
- Checks every requested target against the coolant limits; default water range is **2–40 °C**.
- Monitors in the background and records timestamped CSV data, including failed reads.
- Shows temperature history, sample age, connection faults and recording status.
- Keeps monitoring when the browser closes. All dashboard styles are included locally.

All temperatures are Celsius. Software cannot check installed coolant, flow,
leaks or fluid level. A successful connection does not prove physical safety.

## Five examples

Each script uses the simulator. Run them from the project folder after installation.
Files created by examples 03 and 04 are never overwritten automatically.

| Example | Run after `.\.venv\Scripts\python` | Expected result |
| --- | --- | --- |
| [01 · Read temperature](examples/01_read_temperature.py) | `examples/01_read_temperature.py` | 20.00 °C temperature and target, plus simulator status |
| [02 · Set temperature](examples/02_set_temperature.py) | `examples/02_set_temperature.py` | Target reads 18.00 °C; temperature changes gradually |
| [03 · Log temperature](examples/03_log_temperature.py) | `examples/03_log_temperature.py` | Five samples in `outputs/03_temperature.csv` |
| [04 · Monitor an experiment](examples/04_monitor_experiment.py) | `examples/04_monitor_experiment.py` | Ten seconds of background cooling and CSV recording |
| [05 · Launch dashboard](examples/05_launch_dashboard.py) | `examples/05_launch_dashboard.py --csv outputs/dashboard.csv` | Browser dashboard and CSV recording |

For example:

```powershell
.\.venv\Scripts\python examples/01_read_temperature.py
```

## Continuous monitoring and CSV

To record without a browser until you press Ctrl+C:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --csv outputs/experiment.csv
```

Add `--duration 60` for a one-minute run or `--interval 0.5` to sample twice a
second. CSV contains UTC time, elapsed seconds, temperature, setpoint, status and
errors. Failed readings are blank. Use a new filename, or explicitly add
`--append-csv` to validate and continue an existing file with a new session ID.
The equivalent console command is `.\.venv\Scripts\tark-chiller`.

Ctrl+C stops the worker, disconnects the device and closes CSV. Closing the browser
leaves monitoring running. Forced process termination or power loss cannot guarantee
cleanup. See [recording and shutdown](docs/usage.md#csv-recording).

## Small, explicit API

```python
from tark_chiller import Chiller, SimulatedDevice

chiller = Chiller(SimulatedDevice())
try:
    chiller.connect()
    print(chiller.get_temperature())
    chiller.set_setpoint(18.0)
    print(chiller.get_setpoint())
finally:
    chiller.disconnect()
```

`Chiller` connects, reads and sets the target. `Monitor` starts/stops sampling.
`CsvLogger` writes the data. The GUI reads `monitor.state` and never starts its
own acquisition loop. See the [function-by-function API guide](docs/api.md).

![Software and device paths](docs/assets/modes.svg)

## Real-hardware quick start

**Start with the [hardware tutorial](docs/hardware.md).**
Identify the chiller/controller and obtain its communication manual. The software
then needs a documented protocol implementation and tests before an approved
physical trial. There is currently no hardware command-line mode. Installing
pySerial alone does not enable one, and the default protocol blocks port opening.

[Example 06](examples/06_hardware_configuration.py) is a configuration template,
not a connection command. It accepts explicit `SerialSettings`; RS-232 uses
`RS232Transport`, while RS-485 also requires `RS485Mode`. Baud rate, parity, data/
stop bits, flow control, RTS/DTR, addressing and wiring must come from the matching
controller/adapter documentation. None are supplied as Tark defaults. See the
[configuration table and staged validation procedure](docs/hardware.md).

## Compatibility and validation

| Scope | Status |
| --- | --- |
| Windows / Python 3.12 | Installation, examples, tests, CSV locking and browser use exercised |
| Simulator | API, safe targets, monitoring, CSV and dashboard tested |
| Fake RS-232 / RS-485 | Production transport/device code tested with injected faults; no electrical validation |
| Physical MRC150/300 | **Not validated; matching protocol still missing** |
| Other operating systems / Python versions | Python 3.12+ is declared; other platforms have not been exercised here |

If the browser cannot connect, keep the terminal open and read its error. If a
future serial connection reports a missing/busy port, permission failure or timeout,
check the identified adapter, competing applications and documented settings;
do not retry a temperature write blindly. [Troubleshooting](docs/troubleshooting.md)
explains these errors and CSV/installation problems.

## Guides

[Quick start](docs/quickstart.md) · [Dashboard and CSV](docs/usage.md) ·
[Python API](docs/api.md) · [Hardware tutorial](docs/hardware.md) ·
[Troubleshooting](docs/troubleshooting.md)

For contributors: [development notes, architecture and validation records](development/README.md).
