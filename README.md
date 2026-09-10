# Tark MRC150/300 Python controller

Initialization scaffold with a working simulator, independent acquisition, CSV
logging and a Plotly Dash interface. Real communication is intentionally disabled:
the applicable controller protocol is not available. This is not a hardware-ready
or physically validated release.

## Install and validate

Use Python 3.12 or later in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[gui,serial,dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

`requirements-tested.txt` records the exact tested dependency environment.
Core usage needs no third-party runtime dependencies; install `.[gui]` for Dash
and `.[serial]` for future serial integration. Tests require all extras.

## Simulator

```powershell
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv
```

Open http://127.0.0.1:8050 in a browser. Temperature/setpoint use °C. Default
distilled-water policy allows 2–40 °C, inclusive. The displayed values come from
an uncalibrated simulator; fluid, flow, leak and level telemetry is unavailable.
Press Ctrl+C to stop acquisition, close the CSV and disconnect the simulator.
Each run requires a new CSV filename so existing measurements are not overwritten.

Hardware-free headless acquisition, with no Dash import or browser:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/headless.csv
```

Public API:

```python
from tark_chiller import Chiller, SimulatedDevice

chiller = Chiller(SimulatedDevice())
chiller.connect()
try:
    chiller.set_setpoint(18.0)
    print(chiller.get_temperature(), chiller.get_setpoint(), chiller.get_status())
finally:
    chiller.disconnect()
```

`is_connected` describes the software connection, not proof of physical safety.
Do not infer an installed coolant from the configured profile. Custom profiles
require explicit bounds and a source; pass the same profile to API and backend.
The CLI deliberately offers simulator mode only. Stopping software must not be
interpreted as stopping a physical chiller.

## Engineering workspace

This project uses [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template)
at commit `724a7f772069d3357ea66dbc4742d25bd874a33e`.
Humans define intent in [PROJECT.md](PROJECT.md). The coordinator maintains
[STATE.md](STATE.md), [records/RECORDS.md](records/RECORDS.md) and
[outputs/REPORT.md](outputs/REPORT.md). Session prompts are in [prompt log.md](prompt%20log.md).
The workflow is inspect → gap → design → implement → test → diagnose → update
state → repeat; see [AGENTS.md](AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
The software architecture decision is [D001](records/RECORDS.md#d001).

Resume by reading PROJECT.md, STATE.md and AGENTS.md, then follow the precise next
action and linked test evidence. Missing hardware/protocol information does not
prevent useful simulator/software work. Required physical checks stay outstanding;
candidate review precedes future real-device interaction.
