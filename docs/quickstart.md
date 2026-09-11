# Hardware quick start

[Home](../README.md) · [API](api.md) · [Troubleshooting](troubleshooting.md)

The five examples use the physical serial backend. They never fall back to a
simulator. You can install the software now, but hardware operation is blocked
until the matching controller manual, verified settings and codec are available.

![Document, configure, validate reads, then record](assets/quickstart.svg)

## 1. Install

Use Python 3.12+ and open Windows PowerShell in the folder containing
`pyproject.toml`. Check the version first:

```powershell
python --version
```

If it is older than 3.12, select a supported Python installation and reopen the
terminal. Then:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
```

Use this environment's Python for subsequent commands; activation is unnecessary.

## 2. Configure the lab connection

Open [examples/connection.py](../examples/connection.py).

- `SERIAL_SETTINGS`: a `SerialSettings` object containing the verified port and all ten documented serial fields.
- `CODEC_CLASS`: the implemented and tested codec class for the identified controller.
- `RS485_MODE`: native direction settings when required by the verified RS-485
  adapter; otherwise `None`. This option does not select the electrical interface.

Keep required entries unset while information is missing. There is no codec
included to select. The [hardware guide](hardware.md) explains each field,
manual limitations and the physical review sequence.

## 3. Validate reads before writing

For the reviewed configuration and approved physical setup:

```powershell
.\.venv\Scripts\python examples/01_read_temperature.py
```

It prints measured temperature, reported setpoint and status. Compare the values
and units with the front panel and approved reference. The script makes no
setpoint change.

With the shipped, unconfigured helper, the expected result is **Hardware not
configured**, a nonzero exit and no port opening. That is an intentional dependency
check, not a failed physical test.

## 4. Record readings

After reads are validated:

```powershell
.\.venv\Scripts\python examples/03_log_temperature.py
```

This records for about five seconds. Open `outputs/03_temperature.csv`; check UTC
times, Celsius values, the hardware label and any errors. Each run needs a new
filename, editable in the script.

For continuous read-only recording:

```powershell
.\.venv\Scripts\python examples/04_monitor_experiment.py
```

Press **Ctrl+C** to stop and close `outputs/04_experiment.csv`.

## 5. Change one approved target

Only after read validation and approval for the intended change:

```powershell
$targetC = Read-Host "Approved target in Celsius"
.\.venv\Scripts\python examples/02_set_temperature.py $targetC
```

The script prints the original target, makes one validated write and reads back
the reported target. Readback does not prove the liquid has reached that
temperature. If confirmation fails, resolve the uncertain outcome before a new write.

## 6. Open the optional dashboard

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"
.\.venv\Scripts\python examples/05_launch_dashboard.py
```

Open [127.0.0.1:8050](http://127.0.0.1:8050). Check backend, sample age, errors and
recording status. Apply only approved targets. It records to
`outputs/05_dashboard.csv` without needing the browser to remain open.

Stop with **Ctrl+C in the terminal**. This stops monitoring and closes CSV and
the software connection. Follow the equipment shutdown procedure separately;
disconnect does not switch off the chiller.

[Dashboard tour](usage.md#dashboard-tour) · [API](api.md) ·
[Development-only simulator](../development/README.md#simulator-utilities)
