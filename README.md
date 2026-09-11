# Tark MRC150/300 laboratory controller

A small Python driver for temperature reads, approved target changes and CSV
recording. An optional Dash interface displays live readings and faults.

**Hardware control is blocked by the missing controller communication manual.**
The lab script uses the real serial backend and refuses to create a port until
verified settings and a documented codec are supplied. It never substitutes a
simulator. No physical MRC150/300 has been validated.

## Hardware quick start

Use Python **3.12 or newer**. Open Windows PowerShell in the project folder and
check `python --version`. If it is older than 3.12, select a supported installation
before continuing.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
```

Configure the three entries at the top of [examples/lab.py](examples/lab.py):
`SERIAL_SETTINGS`, `CODEC_CLASS` and optional `RS485_MODE`.
Leave required entries unset while the controller information is missing.
The [hardware guide](docs/hardware.md) explains every field and the review sequence.
Installing pySerial does not supply the missing commands.

After configuration and approval of the physical setup, start with a read:

```powershell
.\.venv\Scripts\python examples/lab.py read
```

It prints temperature, setpoint and status without changing the target.
Compare Celsius readings with the front panel and approved reference.
With the shipped configuration, expect **Hardware not configured**, a nonzero
exit and no port opening.

The same script covers the remaining tasks:

| Command after `python examples/lab.py` | Behavior |
| --- | --- |
| `read` | Read temperature, setpoint and status once |
| `set TARGET_C` | One operator-selected target change and readback |
| `log --csv outputs/run.csv` | Record five seconds without changing the target |
| `monitor --csv outputs/experiment.csv` | Continuous read-only recording until Ctrl+C |
| `gui --csv outputs/dashboard.csv` | Dashboard with recording until Ctrl+C |

`log` requires a CSV path. For `monitor` and `gui`, omit `--csv` if recording
is unnecessary. Existing files are refused unchanged.

For an authorized target change, supply your approved Celsius value:

```powershell
$targetC = Read-Host "Approved target in Celsius"
.\.venv\Scripts\python examples/lab.py set $targetC
```

The script reads the original target, writes once and checks readback. Any numeric
mismatch is reported as unconfirmed; it does not retry or assume a rounding tolerance.

## Use the driver in an experiment

Run from the repository root after lab configuration and approved read validation:

```python
from examples.lab import create_chiller

with create_chiller() as chiller:
    print(chiller.read_temperature())  # Celsius
    print(chiller.read_setpoint())
    print(chiller.read_status())
```

The helper creates a disconnected driver. The context manager connects on entry
and cleans up on exit. Import and construction start no monitoring threads.
[API, monitoring and CSV reference](docs/api.md).

## Optional dashboard

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"
.\.venv\Scripts\python examples/lab.py gui --csv outputs/dashboard.csv
```

Open [127.0.0.1:8050](http://127.0.0.1:8050). Closing the browser leaves acquisition
running. **Ctrl+C in the terminal** stops monitoring and closes CSV and the software
connection. Disconnecting software does not switch off the physical chiller.

![Actual simulator screenshot illustrating the dashboard; not a physical measurement](docs/assets/dashboard.jpg)

*Interface illustration captured with the simulator. See [GUI behavior](docs/api.md#gui).*

## Safety and validation

All targets are validated before backend access. Default distilled-water limits
are **2–40 °C**, from the manual's page 7 table; the lab must confirm applicability.
Custom coolant limits need a documented source. Software cannot detect coolant,
leaks, flow or fluid level. Writes are never retried or replayed automatically.

| Scope | Evidence |
| --- | --- |
| Simulator | Driver, monitoring, CSV and GUI tested without hardware |
| Fake serial | Production serial path and lab commands tested with memory endpoints |
| Physical MRC150/300 | **Not validated; matching controller protocol missing** |
| Windows / Python 3.12 | Development and validation platform; other platforms not exercised |

[API](docs/api.md) · [Hardware configuration](docs/hardware.md) ·
[Troubleshooting](docs/troubleshooting.md) ·
[Development, simulator utilities and records](development/README.md)
