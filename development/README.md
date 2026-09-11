# Development and validation

[User documentation](../README.md) · [Architecture](architecture.md)

The installed package contains the controller, simulator, monitoring, CSV and
optional GUI. Developer-only serial fixtures and the fault demonstration live
here and are not installed in the controller wheel.

## Run the checks

From the repository root, after creating the environment in the quick start:

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

A sustained fault test uses the real transport/device/API/monitor/CSV/Dash code
with a software serial endpoint. Choose a fresh output name:

```powershell
.\.venv\Scripts\python -m development.hardware_free_demo --duration 600 --interval 1 --output outputs/soak
```

It creates CSV, Plotly HTML and a JSON result. Its bytes/settings are deliberately
synthetic; never use this fixture with a physical port. Normal researcher examples
are under [examples](../README.md#five-examples).

## Engineering records

- [Project requirements](../PROJECT.md) and [current state](../STATE.md)
- [Evidence and decisions](../records/RECORDS.md) and [engineering report](../outputs/REPORT.md)
- [Template workflow](../AGENTS.md) and [verbatim prompt log](../prompt%20log.md)

These canonical files retain the original engineering workflow. Historical records
refer to the source layout and software revision at the time of each test; current
commands are listed here. Protocol development is blocked on the matching controller
manual. Physical work requires a separately reviewed candidate and authorization.
