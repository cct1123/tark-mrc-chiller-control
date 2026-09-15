# Development

[User quick start](../README.md#hardware-quick-start) · [API](../docs/api.md)

## Architecture

Six package files keep the driver independent from its optional clients.

| File | Responsibility |
| --- | --- |
| controller.py | Chiller API, validation/recovery, Status, Simulator and ProtocolError |
| serial.py | Serial settings/transport, Cal33xx protocol and CalSimulator memory endpoint |
| monitor.py | Optional worker, immutable snapshots, bounded history and new-file CSV |
| gui.py | Optional display and validated target controls |
| __init__.py | Public exports |
| __main__.py | Simulator utility launcher |

![Driver ownership, backend paths and optional data flow](../docs/assets/system.svg)

Application → Chiller → SerialDevice/codec, or Simulator for development.
Chiller serializes device access and validates public target requests. Raw backend
hooks are private. One backend and codec belong to one Chiller; no global owner
registry or configuration framework is used.

The optional worker reads through Chiller, owns its CSV/history and exposes
`snapshot()`. Running state comes from the worker thread. Status, Sample and
Snapshot keep named fields. The serial codec returns a diagnostic string for
`get_status`; SerialDevice constructs the public Status. Codec output does not
define local connection ownership.

`stop_monitoring()` joins and closes CSV while preserving connection.
`disconnect()` also cancels recovery and closes the backend. CSV cleanup occurs
outside the snapshot lock. Timeouts remain errors; OS calls that ignore them and
forced process termination cannot guarantee cleanup. Settings and monitoring
interval are read-only.

Researcher configuration and commands live in one editable
[examples/lab.py](../examples/lab.py). It creates a fresh hardware driver/codec
per call, with no simulator fallback. GUI construction starts no acquisition.
The CAL protocol subset is implemented; actual installed identity and physical
acceptance remain external. See [protocol sources and scope](../docs/protocol.md).

## Simulator utilities

Simulation is a development tool. The module launcher always selects it:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
.\.venv\Scripts\python -m tark_chiller --cal --csv outputs/simulator-dashboard.csv
```

Open [127.0.0.1:8050](http://127.0.0.1:8050), then stop with Ctrl+C.
For a short browser-free run:

```powershell
.\.venv\Scripts\python -m tark_chiller --cal --headless --duration 5 --interval 0.2 --csv outputs/simulator-timed.csv
```

Omit duration to run until Ctrl+C; each recording needs a new file.
The CAL model starts with synthetic 20 °C liquid and the manual's 10 °C target.
It uses the production serial/command path and keeps controlling after serial
close. Omitting --cal selects the simpler API simulator (20 °C initial target,
pauses while disconnected). Neither predicts physical cooling or fluid safety.

## Checks

From the repository root after creating the README environment:

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

Tests use simulators and memory endpoints with production code. CAL tests include
published request/reply bytes and interrupted security/write sequences; generic
transport tests retain clearly synthetic bytes. No test opens an OS serial port.
Simulator settings must not enter lab configuration.
For a sustained simulator/CSV/Dash run:

```powershell
$env:TARK_SOAK_SECONDS = '600'
.\.venv\Scripts\python -m pytest tests/test_end_to_end.py::test_simulator_monitor_csv_dash -s
Remove-Item Env:TARK_SOAK_SECONDS
```

The regular suite uses a short run and separately exercises serial faults.

## Records

[Requirements](../PROJECT.md), [state](../STATE.md), [evidence](../records/RECORDS.md),
[report](../outputs/REPORT.md), [workflow](../AGENTS.md) and
[prompt log](../prompt%20log.md) preserve engineering continuity.
Historical results apply to their recorded revision. Physical work requires the
matching protocol, reviewed candidate and specific authorization.
