# Tark MRC150/300 Python controller

Hardware-independent controller with a fault-capable simulator, independent acquisition,
appendable CSV recording and a Plotly Dash interface. Real communication is disabled:
the applicable controller protocol is not available. This is not a hardware-ready
or physically validated release.

## Install and validate

Use Python 3.12 or later in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[gui,serial,dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m ruff format --check src tests examples
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\python -m build --no-isolation
```

For the exact reviewed environment, install `-r requirements-tested.txt` before
installing `-e ".[gui,serial,dev]" --no-deps` into a fresh virtual environment.
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
Existing files are refused unless `--append-csv` is explicit. Append validates the
complete header and records before writing, adds a new session ID and refuses
truncated data unchanged. Old CSVs without session IDs require a new file.
An OS file lock refuses a second logger for the same file, including other
processes and file aliases. Ordinary readers can inspect flushed rows while logging.
Rows are flushed, but power-loss durability is not guaranteed.

Hardware-free headless acquisition, with no Dash import or browser:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/headless.csv
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --csv outputs/headless.csv --append-csv
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

Optional simulation settings: `--noise 0.01 --seed 7 --simulation-interval 0.2`.
The noise is seeded measurement variation, not a physical accuracy specification.
Polling (`--interval`) and measurement cadence are independent. History is bounded
by `--history` (default 3,600 samples). Scheduling is not hard real time.

Read/connect recovery is opt-in: `Chiller(device, recovery=RecoveryPolicy(3, 0.05))`
or CLI `--reconnect-attempts 3`. Import `RecoveryPolicy` from `tark_chiller`.
The budget persists across a failed outage; success resets it, and exhaustion
requires explicit `connect()` or GUI **Connect / retry**. Protocol errors suspend
automatic recovery. A failed write is never replayed, even if its acknowledgement
was lost; resolve uncertainty with readback. Queued writes are cancelled when a
new explicit connect/disconnect supersedes their connection intent.
**Disconnect** cancels recovery and
leaves acquisition recording unavailable readings. Device connection and monitor
lifecycle are separate.

Share one Chiller instance per device. Only one Monitor can actively own a Chiller
or LiveState. `poll_once()` rejects calls while its worker or another poll is active.
Applications own cleanup: `monitor.request_stop()`, `chiller.disconnect()`,
`monitor.stop()`, then `logger.close()`. Disconnect cancels recovery waits and
serializes close with active operations. Do not close a logger while its worker
is still running. OS/third-party calls that ignore their timeouts cannot be
forcibly interrupted; a stop timeout is reported explicitly.

## Hardware-free end-to-end demonstration

```powershell
.\.venv\Scripts\python examples/hardware_free_demo.py --duration 600 --interval 1 --output outputs/soak
```

This runs simulator → synthetic serial endpoint → real transport/device classes
→ API → monitor → CSV/state → concurrent Dash HTTP callbacks. It injects timeouts,
malformed replies, disconnects and a lost write acknowledgement. Output is a CSV,
standalone Plotly trajectory and JSON acceptance summary. Use a new output name.
`python -m pytest` also runs a five-second version. Intervals are software choices;
Windows scheduler granularity makes very short polling intervals inaccurate.

Reusable fixtures: `tark_chiller.testing.make_fake_device()` returns a device and
in-memory endpoint. Wrap that device in the same `Chiller` API; inject faults with
`endpoint.inject_fault("get_temperature", "timeout")`. The fake protocol is labeled
synthetic and has no relation to Tark commands or settings. The default real
`SerialDevice` still fails before opening a port until an authoritative codec exists.

## Engineering workspace

This project uses [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template)
at commit `724a7f772069d3357ea66dbc4742d25bd874a33e`.
Humans define intent in [PROJECT.md](PROJECT.md). The coordinator maintains
[STATE.md](STATE.md), [records/RECORDS.md](records/RECORDS.md) and
[outputs/REPORT.md](outputs/REPORT.md). Session prompts are in [prompt log.md](prompt%20log.md).
The workflow is inspect → gap → design → implement → test → diagnose → update
state → repeat; see [AGENTS.md](AGENTS.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
Current ownership and cleanup decisions are in [D003](records/RECORDS.md#d003).

Resume by reading PROJECT.md, STATE.md and AGENTS.md, then follow the precise next
action and linked test evidence. Missing hardware/protocol information does not
prevent useful simulator/software work. Required physical checks stay outstanding;
candidate review precedes future real-device interaction.
