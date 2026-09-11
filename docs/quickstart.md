# Quick start: run the simulator

[Home](../README.md) · [User guide](usage.md) · [Troubleshooting](troubleshooting.md)

You need **Python 3.12 or newer**, a copy of this repository and internet access
for installation. No chiller or serial adapter is needed. These instructions use
Windows PowerShell and were tested with Python 3.12.

Download and extract the repository, or use your lab's existing copy. Open
PowerShell in the project folder containing `pyproject.toml`. Run all commands
below from that folder. Copy only the command text into the terminal.

![Local simulator setup: install, launch, open browser and record CSV](assets/quickstart.svg)

## 1. Install once

```powershell
python --version
```

**Stop here if Python is missing or older than 3.12.** Install or select Python
3.12 or newer, reopen PowerShell if needed, and check again. Then create a local
Python environment and install the app with its dashboard packages:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
```

The `.venv` folder holds this project's Python and packages. The commands call
that Python directly, so you do not need to activate the environment. Reuse it
on later runs; do not repeat installation each time.

This installs a normal package copy with the tested dependency versions. After
updating the project, repeat the install command. For headless/API use only,
install `.` instead of `".[gui]"`; the core has no third-party dependencies.
On Linux/macOS, use `.venv/bin/python` in place of the Windows Python path;
those operating systems have not been validated by this release.

## 2. Start the dashboard and recording

```powershell
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv
```

Leave the terminal running. Open [http://127.0.0.1:8050](http://127.0.0.1:8050)
on the same computer. Check that the page says **simulator**, **Monitoring**
and **CSV Enabled**. Sample and row counts should increase about once a second.
The app creates the `outputs` folder inside your current project folder. Choose
a new CSV filename for each new run.

## 3. Try a safe setpoint

Enter **18** in **Requested temperature (°C)** and click **Apply setpoint**.
The target should read 18.00 °C on the next update, while the simulated temperature
falls gradually from 20 °C. The solid line is temperature; the dashed line is
setpoint. Entering **1** is rejected by the default 2–40 °C limits.

## 4. Stop and inspect your data

Press **Ctrl+C in the terminal**. This stops monitoring and closes the CSV file.
Closing only the browser tab leaves recording running. Open
`outputs/session.csv` in a spreadsheet or text editor after stopping.

To add new readings to an existing, undamaged CSV:

```powershell
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv --append-csv
```

This starts a **new simulator session** at 20 °C and adds a new session ID. It
keeps the old rows, but starts a fresh plot and resets the target to 20 °C.
Without `--append-csv`, the app refuses an existing file. Stop this run with Ctrl+C too.

## Without a browser

For continuous recording, omit `--duration` and stop with Ctrl+C:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --csv outputs/continuous.csv
```

For a short timed recording:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/headless.csv
```

This records about once every 0.2 seconds for five seconds, then exits. Exact
timing depends on your computer. For CSV fields, Python examples and status
messages, continue to the [user guide](usage.md).

Run `.\.venv\Scripts\python -m tark_chiller --help` to see the small set of
options. `--duration` requires `--headless`; without `--headless`, the app serves
the dashboard until stopped. `.\.venv\Scripts\tark-chiller` is the same launcher.
