# Engineering report — laboratory driver 0.3.0

## Objective and result

Provide a compact, reusable Python chiller driver that a researcher can understand
and integrate into an existing experiment. This pass reduces the package from
**8 to 6 Python files**, **13 to 11 classes**, and **1,311 to 1,220 lines**. One lab
script replaces six example files; five guides replace eight. Two SVG diagrams remain.
Superseded output artifacts are linked at their committed revision in the records.

All hardware-independent acceptance passes. The matching controller protocol is
still missing; no physical port was opened and real MRC operation is not validated.
[STATE](../STATE.md) maps every requirement to current evidence.

The [README](../README.md) now leads with hardware installation/configuration,
verified reads, recording, approved target changes and dashboard use. Simulator
practice is optional. [E039](../records/RECORDS.md#e039) records the documentation
review and corrections; the hardware prerequisites and validation scope are unchanged.

## Architecture

![Driver ownership and optional clients](../docs/assets/system.svg)

- controller.py: synchronous Chiller API, validation/recovery, Simulator, Status
  and the sole custom exception, ProtocolError.
- serial.py: explicit configuration, codec interface and bounded serial I/O.
- monitor.py: optional polling, CSV recording and bounded immutable snapshots.
- gui.py: optional Dash client. The two entry files export the API and launch
  development simulator utilities.

Construction performs no I/O. Chiller owns its backend and optional monitor;
Dash reads snapshots and submits targets through the public API. Monitoring/CSV
are optional, and core installation has no dependencies. Shared validators and
thread-derived running state replace duplicate logic/state. Status text from the
codec is separate from the driver's local connection state. Required locks,
cancellation, timeouts and no-replay guards remain.

Removed internal modules and ProtocolUnavailableError have no compatibility shims.
Use public imports from tark_chiller, and ProtocolError for missing/invalid protocol.
[API reference](../docs/api.md), [development architecture](../development/README.md#architecture),
[decision and review](../records/RECORDS.md#d008).

## Validation evidence

| Check | Current result |
| --- | --- |
| Fresh normal installation, Python 3.12.14 / Windows | 218 PASS in 15.48 s; [JUnit](tests.xml) |
| Extracted source archive against installed wheel | 218 PASS in 15.79 s; [JUnit](archive-tests.xml) |
| Lint, format, types and dependencies | Ruff, mypy six modules and pip check PASS |
| Build and inventory | Wheel/source archive, package byte integrity and stale-file checks; [build](build.txt), [audit](validation.json) |
| Sustained simulator, monitoring, CSV and concurrent Dash | 60 s, 1,732 rows, 1,527 callbacks, history cap 25, no failed polls, clean shutdown; [run](soak.txt) |
| Hardware lab workflows | Production SerialDevice with synthetic in-memory endpoints; no physical compatibility claim |
| Documentation | README/API blocks execute through the serial path; current links and SVGs checked; revised diagram inspected in a browser |

Fault coverage includes missing/denied/busy ports, partial writes, timeouts,
malformed/truncated/unexpected/delayed replies, disconnects, finite reconnects,
pending-read shutdown, uncertain writes, stale queued targets, duplicate monitors,
disk errors and CSV non-overwrite. Added checks cover malformed decoded status,
failed second readings and invalid lab command combinations before connection.
Independent review found no consequential migration defect or useful further
pruning. Full suites each emitted one upstream Plotly deprecation warning; no map
trace is used. The actual GUI screenshot is unchanged and labeled simulator data.

## Operating instructions

From the repository root in PowerShell, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
.\.venv\Scripts\python examples/lab.py read
```

The shipped lab settings intentionally report **Hardware not configured**, exit
nonzero and open no port. Once the documented codec/settings and physical candidate
are approved, configure SERIAL_SETTINGS, CODEC_CLASS and optional RS485_MODE in
examples/lab.py. Each create_chiller() call creates its own disconnected driver and
codec. Applications can construct Chiller(SerialDevice(...)) directly.

After read-only validation:

```powershell
.\.venv\Scripts\python examples/lab.py log --csv outputs/run.csv
.\.venv\Scripts\python examples/lab.py monitor --csv outputs/experiment.csv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"
.\.venv\Scripts\python examples/lab.py gui --csv outputs/dashboard.csv
```

Log records five seconds; monitor and GUI run until Ctrl+C. Open
[127.0.0.1:8050](http://127.0.0.1:8050) for the GUI. Existing CSV files are refused.
Omit --csv for monitoring/display without recording. Ctrl+C closes the worker,
CSV and connection; closing the browser does not. Software disconnect does not
power off the physical chiller. A stop timeout reports that cleanup remains pending.

For a specifically authorized target change:

```powershell
$targetC = Read-Host "Approved target in Celsius"
.\.venv\Scripts\python examples/lab.py set $targetC
```

The script reads the original, writes once and compares readback exactly. A mismatch
is unconfirmed, with no inferred rounding tolerance or retry. Default
software limits are 2–40 °C; custom coolant bounds need a name and documented source.

For hardware-free development, the separate module launcher always selects the simulator:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --duration 5 --interval 0.2 --csv outputs/simulator.csv
```

See [user quick start](../README.md#hardware-quick-start), [API](../docs/api.md),
[troubleshooting](../docs/troubleshooting.md) and [complete checks](../development/README.md#checks).

## Protocol, physical status and resumption

The official [MRC150/300 User Manual Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf)
provides distilled-water limits 2–40 °C in the page 7 table. Page 12 confirms
RS232/RS485 availability but delegates communication details to a separate manual.
Confirm the interface variant and applicable coolant guidance for the actual unit.
The original attachment is unavailable; the cached official PDF matches E002's hash.
No Tark commands/settings or coolant/flow/leak/level telemetry were invented.

Only physical parts of REQ-002/006/020 and REQ-015 remain BLOCKED by EXT-001/003:

1. Obtain the matching controller manual and unit/suffix/controller/firmware identity.
   Establish baud/parity/data/stop bits, flow control, RTS/DTR, RS485 address/direction,
   syntax/registers, framing/terminators, temperature/setpoint reads, setpoint write,
   acknowledgements/errors, checksum/CRC if any, units/scaling/resolution, response
   identity, timing and side effects.
2. Implement only documented codec behavior in serial.py. Cite source pages and
   add exact byte fixtures. Configure examples/lab.py and rerun software acceptance.
3. Record actual wiring/interface, coolant, safe setup and reference instrument.
   Obtain candidate review before opening a port. No candidate approval exists yet.
4. Follow the [hardware procedure](../docs/hardware.md): approved interface checks,
   repeated temperature comparison, setpoint reads and read-only logging.
5. Only with explicit write authorization, choose one small safe change, send once,
   compare readback, observe response and restore the original if approved. Record
   configuration, raw replies and measurements. Never override software limits.

Polling is not real time; the simulator is uncalibrated. Other platforms, electrical
RS485 behavior and physical chiller performance remain unvalidated. Forced process
termination, uncooperative OS calls and power loss cannot guarantee cleanup or data
durability. Remaining physical dependencies are not failed software tests.
