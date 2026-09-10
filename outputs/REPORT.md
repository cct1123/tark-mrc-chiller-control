# MRC150/300 controller — initialization report

Status: **SOFTWARE_DEVELOPMENT**, initialization/design milestone complete, 2026-09-10.

## Objective and outcome

Established the controller project using the requested template at commit
`724a7f772069d3357ea66dbc4742d25bd874a33e`. The executable scaffold includes the
common API, safe coolant policy, simulator, generic RS232/RS485 adapters,
independent monitoring, CSV, bounded live state and Plotly Dash.

The actual controller protocol is absent. The default physical adapter refuses
connection before opening a port. No real device has been accessed or validated.
The unavailable attachment is distinguished from the official online manual used
for provisional fact extraction.

## Resulting system

```text
Application / Chiller API (serialized, validates before I/O)
  └─ ChillerDevice contract
       ├─ SimulatedDevice (deterministic first-order model)
       └─ SerialDevice
            ├─ ProtocolCodec / MissingProtocol  ← protocol insertion point
            └─ Transport: RS232 / RS485        ← explicit settings only

Monitor worker → Chiller API
Monitor worker → CSV + immutable snapshots / bounded history
Dash refresh   → shared snapshots
Dash submission → Chiller API
```

API: `connect()`, `disconnect()`, `is_connected`, `get_temperature()`,
`get_setpoint()`, `set_setpoint(value_c)`, `get_status()`.
Status describes communication/backend, not invented physical safety flags.
Reads can reveal temperatures outside policy; only requested writes are bounded
so anomalous readings are not concealed. Design: [D001](../records/RECORDS.md#d001).

## Requirements and validation

Full acceptance matrix: [PROJECT.md](../PROJECT.md#requirements--acceptance-criteria).
Current outcomes: [STATE.md](../STATE.md#requirements-status).

| Requirements | Milestone outcome | Evidence |
| --- | --- | --- |
| REQ-001 | PASS: workspace, prompt log, IDs, state/report/ledger, next action | E001, E008 |
| REQ-002 | Software API PASS; real-unit portion BLOCKED | E005; EXT-001/003 |
| REQ-003/004/005 | PASS: range/type guards, explicit profiles, no unknown-protocol I/O | E002, E005 |
| REQ-006 | Software adapters PASS; real interface/electrical support BLOCKED | E005, E007; EXT-001/003 |
| REQ-007/008/009/010 | PASS: faults/lifecycle, independent acquisition, CSV, bounded/freshness-aware state | E004, E005 |
| REQ-011/012/013 | PASS: simulator integration, Dash and unavailable-telemetry labeling | E005, E006 |
| REQ-014 | PASS: install, dependencies/static checks, distribution smoke | E007 |
| REQ-015 | BLOCKED: missing protocol/setup/reference and physical acceptance | EXT-001/003 |

**133 tests passed**, zero failures/errors; [review regression results](precommit-tests.xml).
Ruff check/format passed (17 Python files). Source archive/wheel build and
wheel-only API smoke passed. Browser verified 18 °C simulator readback/cooling,
rejected invalid input and retained history after reload. Review corrected
timestamps, stale service errors, thread-start rollback and extreme timing-value
validation; regressions passed. See [E009 precommit review](../records/RECORDS.md#e009).
[Test methods/evidence](../records/RECORDS.md#validation-methods) delimit these claims.

## Working configuration and calibration

Windows, Python 3.12.14, Dash 4.4.1, Plotly 6.9.0, pySerial 3.5, pytest 9.1.1,
Ruff 0.16.7. [Exact dependencies](../requirements-tested.txt);
[source/config hashes](source-manifest.sha256).
No Tark baud/framing/pinout/controller identity is configured or asserted.

Simulator: initial/target 20 °C, time constant 30 s; polling/GUI refresh 1 s,
history 3,600 samples. These are software choices. No physical calibration,
accuracy or thermal performance is established.

## Operating instructions

From the project root, Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-tested.txt
.\.venv\Scripts\python -m pip install -e ".[gui,serial,dev]"
.\.venv\Scripts\python -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python -m ruff check --no-cache src tests
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv
```

Open [local Dash](http://127.0.0.1:8050). A new CSV filename is required each run;
existing measurements are refused. Without Dash or a browser:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/headless.csv
```

Ctrl+C stops/joins monitoring, disconnects simulator and closes CSV. Disconnect
does not stop physical equipment. Reconnect explicitly after resolving a link
failure; uncertain writes are never automatically retried. CSV failure disables
recording for the run and leaves a persistent error while live monitoring continues.
Correct storage and restart with a new file. If worker stop times out, do not close
its device/logger until joined. API example: [README.md](../README.md).

Build: `.venv\Scripts\python -m build --no-isolation` after dev install.
In this managed session, temporary files, local socket and built-wheel access
required approved tool escalation; tests were not weakened. This does not imply
administrator rights are required on a normal workstation.

## Limits and hardware integration preparation

No hardware-ready review is requested. Next independent work is the sustained
fault-injected run specified in [STATE.md](../STATE.md#current-priority-and-precise-next-engineering-action).

The later candidate needs the matching communications manual, actual unit/
controller/firmware/interface identity, verified initialization/read/write
semantics, units, golden protocol vectors, physical setup and coolant suitability.
EXT-001/003 prevent writing exact first hardware commands or test tolerances now.
TEST-012 records remaining stages without pretending they have been executed.

Default water policy is grounded in [E002](../records/RECORDS.md#e002). Other profiles
require evidence for actual liquid/unit; configuration cannot detect coolant,
leaks, flow or level. No alternate coolant preset is enabled automatically.
An RS485 software class does not establish actual RS485 hardware availability.

Other limits: short tests do not prove sustained reliability; readings are
sequential with conservative timestamps, not atomic hardware snapshots; stale age
uses host UTC clock; native serial mode/OS timing depend on platform. No automatic
reconnect, command replay, physical start/stop, PID/alarm configuration, cloud
deployment or multi-process control. Local Dash server is single-process/loopback.

## Important decisions and references

- [Template](https://github.com/cct1123/agentic-engineering-template), unchanged
  [AGENTS.md](../AGENTS.md) and [ARCHITECTURE.md](../ARCHITECTURE.md)
- [Manual provenance/facts](../records/RECORDS.md#e002), separate from
  [engineering assumptions](../PROJECT.md#assumptions--engineering-choices)
- [D001 architecture](../records/RECORDS.md#d001), [E004 review](../records/RECORDS.md#e004),
  [E005 tests](../records/RECORDS.md#e005), [E006 browser](../records/RECORDS.md#e006),
  [E007 packaging](../records/RECORDS.md#e007)
