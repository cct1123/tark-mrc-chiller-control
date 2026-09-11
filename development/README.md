# Development and validation

[User documentation](../README.md) · [Architecture](architecture.md)

The installed package contains the driver, serial backend, optional monitoring/
CSV and optional GUI. Researcher examples use the physical backend configured in
`examples/connection.py`. Their default configuration refuses port creation;
tests inject memory endpoints to exercise the same production path.

## Simulator utilities

Simulation is a development tool, not a fallback in the hardware examples.
After creating the quick-start environment, install the optional GUI if needed:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
.\.venv\Scripts\python -m tark_chiller --csv outputs/simulator-dashboard.csv
```

Open [127.0.0.1:8050](http://127.0.0.1:8050). This module launcher always uses the
simulator. Its CSV and dashboard must not be described as physical measurements.

For a timed software-only recording:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/simulator-timed.csv
```

Omit `--duration` to run until Ctrl+C. Each run needs a new CSV filename.
The headless launcher exits with an error if recording fails.
The equivalent console command is `.\.venv\Scripts\tark-chiller`.

`Simulator()` starts at 20 °C with a 20 °C target. Its uncalibrated thermal model
approaches a changed target gradually and pauses while disconnected. It predicts
neither physical cooling performance nor coolant/flow safety.

## Run the checks

From the repository root:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-tested.txt
.\.venv\Scripts\python -m pip install -e ".[gui,serial,dev]" --no-deps
.\.venv\Scripts\python -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check src tests examples development
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m build --no-isolation
```

Tests exercise production code with simulator or memory backends. Synthetic
settings and messages establish no electrical compatibility and must never be
copied into the lab configuration.

For a ten-minute simulator/CSV/Dash integration run:

```powershell
$env:TARK_SOAK_SECONDS = '600'
.\.venv\Scripts\python -m pytest tests/test_end_to_end.py::test_simulator_monitor_csv_dash -s
Remove-Item Env:TARK_SOAK_SECONDS
```

The normal suite uses a short run. Its separate
`test_fake_serial_monitor_reports_outage_and_recovers` test injects a cable fault
into a memory endpoint; neither test touches hardware.

## Engineering records

- [Requirements](../PROJECT.md) and [current state](../STATE.md)
- [Evidence/decisions](../records/RECORDS.md) and [report](../outputs/REPORT.md)
- [Engineering workflow](../AGENTS.md) and [prompt log](../prompt%20log.md)

Historical evidence applies to its recorded revision. Current checks distinguish
simulator tests, fake-serial tests and absent physical validation.
The matching communication manual remains an external dependency; physical work
requires a reviewed candidate and specific authorization.
