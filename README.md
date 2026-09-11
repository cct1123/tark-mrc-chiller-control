# Tark MRC150/300 laboratory controller

A small Python driver for reading temperature, changing an approved target, and
recording measurements from a Tark MRC150/300. The optional Dash interface shows
live readings and recording status.

**The hardware protocol is not implemented yet.** The MRC user manual refers to
a separate controller communication manual, which is still missing. The examples
use the serial backend and stop with a configuration error until verified settings
and a documented codec are supplied. They never substitute a simulator.

## Install and configure

Use Python **3.12 or newer**. In Windows PowerShell, open the project folder and
check `python --version`, then:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
```

Configure [examples/connection.py](examples/connection.py) once for the lab.
It has three entries: `SERIAL_SETTINGS`, `CODEC_CLASS` and optional
`RS485_MODE`. Required entries remain `None` until their values are verified.
See the [quick start](docs/quickstart.md) and
[serial settings table](docs/hardware.md#serialsettings-fields).

Installing pySerial does not supply the missing controller commands. Physical
use also needs the identified unit, suitable coolant/setup and a reviewed test plan.

## Use it in your experiment

After configuration and approved read-only validation, run this from the
repository root:

```python
from examples.connection import create_chiller

with create_chiller() as chiller:
    print(chiller.read_temperature())  # Celsius
    print(chiller.read_setpoint())
    print(chiller.read_status())
```

The helper returns a disconnected `Chiller`. The context manager connects on
entry and closes the connection on exit. Import and construction start no
monitoring threads. [Function-by-function API](docs/api.md).

## Numbered hardware examples

Run these from the project folder after configuration. Examples 01, 03 and 04
read without changing the target. Example 05 writes only when you apply a target
in the dashboard.

| Example | Purpose |
| --- | --- |
| [01 · Read temperature](examples/01_read_temperature.py) | One temperature, setpoint and status read |
| [02 · Set temperature](examples/02_set_temperature.py) | One operator-selected target change and readback |
| [03 · Log temperature](examples/03_log_temperature.py) | Five seconds to `outputs/03_temperature.csv` |
| [04 · Monitor an experiment](examples/04_monitor_experiment.py) | Continuous read-only monitoring to `outputs/04_experiment.csv`; Ctrl+C stops |
| [05 · Launch dashboard](examples/05_launch_dashboard.py) | Physical-device display and `outputs/05_dashboard.csv`; Ctrl+C stops |

Start with:

```powershell
.\.venv\Scripts\python examples/01_read_temperature.py
```

With the shipped configuration, expect **Hardware not configured** and no port
opening. With a reviewed configuration, compare the reported Celsius readings
against the front panel and approved reference.

For an authorized target change, supply your approved value rather than copying
a fixed temperature:

```powershell
$targetC = Read-Host "Approved target in Celsius"
.\.venv\Scripts\python examples/02_set_temperature.py $targetC
```

## Monitoring and dashboard

```python
from time import sleep
from examples.connection import create_chiller

with create_chiller() as chiller:
    monitor = chiller.start_monitoring(csv_path="experiment.csv")
    sleep(5)
    chiller.stop_monitoring()
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(snapshot.logged_samples)
```

Monitoring is optional and independent of the browser. Each run creates a new
CSV; existing files are refused. `stop_monitoring()` closes recording and keeps
the device connected. `disconnect()` also stops monitoring and closes CSV.

Install the GUI extra, then use the hardware dashboard example:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"
.\.venv\Scripts\python examples/05_launch_dashboard.py
```

Open [127.0.0.1:8050](http://127.0.0.1:8050). Stop with **Ctrl+C in the terminal**.
Closing the browser leaves recording running.

![Dashboard appearance captured with the simulator; not a physical measurement](docs/assets/dashboard.jpg)

*Actual simulator screenshot for interface illustration. No hardware validation
is implied. [Dashboard and CSV guide](docs/usage.md).*

## Safety and validation

All values are Celsius. Every target is checked before backend access.
Default distilled-water limits are **2–40 °C**, from the manual's page 7 table;
the lab must confirm their applicability. Custom coolant limits need a documented
source. Software cannot detect installed coolant, leaks, flow or fluid level.
Writes are never retried or replayed automatically.

| Scope | Evidence |
| --- | --- |
| Simulator | Driver, monitoring, CSV and GUI tested without hardware |
| Fake serial | Production serial path and hardware examples tested with memory endpoints |
| Physical MRC150/300 | **Not validated; matching controller protocol missing** |
| Windows / Python 3.12 | Development and validation platform |
| Other platforms | Not exercised here; Python 3.12+ declared |

[Quick start](docs/quickstart.md) · [Hardware setup](docs/hardware.md) ·
[API](docs/api.md) · [Dashboard and CSV](docs/usage.md) ·
[Troubleshooting](docs/troubleshooting.md)

[Development notes](development/README.md) contain simulator utilities, tests and
engineering records.
