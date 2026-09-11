# Development and validation

[User documentation](../README.md) · [Architecture](architecture.md)

The v0.2 package is a synchronous Chiller driver with a simulator, optional
monitoring/CSV and optional GUI. Fake endpoints belong in tests, not the installed
driver. The earlier demonstration framework and multi-object application API have
been removed.

## Run the checks

From the repository root, after creating the quick-start environment:

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

Tests exercise production driver/serial/monitor/GUI code with simulator or memory
backends. Synthetic settings and messages establish no electrical compatibility.
Run researcher scripts from [examples](../README.md#numbered-examples).

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

Historical records describe their own revisions. Current acceptance must refer
to the v0.2 implementation and its tests. The matching controller protocol remains
an external dependency; physical interaction requires a reviewed candidate and
specific authorization.
