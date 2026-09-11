# Engineering report — software release candidate

## Objective and outcome

Deliver a small laboratory controller with a common Python API, independent
monitoring, CSV recording and a clear Dash display. **The software release
candidate is complete: 401 tests and the ten-minute fault run pass.** Physical
MRC150/300 operation remains unavailable pending its matching communication
protocol and approved validation. Package version 0.1.0; Windows/Python 3.12.14.

## Implemented architecture

![Controller architecture](../docs/assets/system.svg)

Dash reads bounded shared snapshots and submits controls through Chiller. One
Monitor samples the same API and sends rows to CsvLogger. SimulatedDevice and
SerialDevice implement the device boundary; the latter composes the protocol
codec and explicit RS232/RS485 transport. MissingProtocol refuses connection
before port creation. No acquisition worker belongs to Dash.

The public control API remains connect/disconnect, is_connected, temperature
read, setpoint read/write and status read. Monitor starts/stops sampling; CsvLogger
opens/closes recording. Developer fixtures are outside the installed package.
The core has no third-party dependencies; Dash, Plotly, Bootstrap components and
pySerial are optional. GUI styling is local, without a runtime CDN.

## Requirements audit and corrections

The coordinator read the project, state, instructions, complete implementation,
tests, records and available manual before editing. E026's 38 entry hashes matched,
but independent review still reproduced four interruption defects: lost monitoring
ownership during startup, unclosed serial transactions, silent worker termination
and reuse of an uncertain CSV writer. Each was repaired with a failing regression
before the complete suite was rerun. No public API layer was added.

Headless recording now runs until Ctrl+C when --duration is omitted. A timed run
requires --headless. Missing GUI packages produce an installation message before
CSV creation. Cooperative termination uses the normal ordered shutdown path.
The configuration template uses current API types without invented serial values.
README/guides cover installation, API, recording, safe targets and future hardware
work; the refreshed screenshot is an actual simulator session.

All hardware-independent criteria REQ-001–014 and REQ-016–022 are **PASS**.
Physical portions of REQ-002/006 and REQ-015 are **BLOCKED** by EXT-001/003.
The complete criterion-by-criterion matrix is in [STATE](../STATE.md), with methods
and reproduced defects in [E027/E028 and D005](../records/RECORDS.md#e027).

## Validation evidence

| Check | Observed result |
| --- | --- |
| Full unit/integration/fault suite | 401 PASS in 27.55 s; [JUnit](release-tests.xml) |
| Fresh installation | Python 3.12.14, all 43 pinned dependencies installed afresh; no shared third-party paths |
| Static checks | Ruff lint/format PASS; mypy 13 modules PASS; pip check PASS |
| Package | Source archive and wheel build/install PASS; 17 package files identical to tested source; [integrity](release-package.json), [build](release-build.txt) |
| Ten-minute complete-stack fault run | 596 samples/CSV rows, history bounded at 120, 31 unavailable polls, 4,002 refreshes, 161 page reloads; [summary](release-soak.json) |
| Writes and recovery in that run | 401 unsafe requests rejected; exactly three applied writes and seven planned opens; uncertain write not replayed; final 18.014839 °C at target 18 °C |
| Simulator browser | Safe/unsafe controls, disconnect/reconnect and reload PASS; 360 CSV rows, including 37 after tab closure; Ctrl+C stopped sampling; [operations](release-operations.json) |
| Researcher workflow | Normal install, module/console commands, headless recording/append, README Python, numbered examples and blocked hardware template PASS; rendered guides/images/links checked |

The [source manifest](release-source-manifest.sha256) identifies 40 source/test/
example/configuration files. [Exact dependencies](../requirements-tested.txt)
include Dash 4.4.1, Plotly 6.9.0, dash-bootstrap-components 2.0.4 and pySerial 3.5.
All serial tests use memory endpoints and explicitly synthetic fixture bytes.
They do not establish electrical compatibility or physical performance.

## Exact operating instructions

In the project folder, with Python 3.12 or newer selected, use Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
.\.venv\Scripts\python -m tark_chiller --csv outputs/session.csv
```

Open [127.0.0.1:8050](http://127.0.0.1:8050). Confirm simulator/Monitoring/CSV
Enabled; try an 18 °C target. Default water limits are 2–40 °C. All numeric inputs
mean Celsius; strings, booleans, NaN, infinity and out-of-range targets are rejected.
Software cannot identify coolant or prove sub-zero safety. Setpoints are never
automatically replayed on reconnect.

For continuous recording without a browser:

```powershell
.\.venv\Scripts\python -m tark_chiller --headless --csv outputs/experiment.csv
```

Add --duration 60 for one minute or --interval 0.5 for twice-per-second sampling.
Use a fresh filename; --append-csv explicitly validates and adds a new session to
an existing file. The console entry point .\.venv\Scripts\tark-chiller is equivalent.
Press **Ctrl+C in the terminal**: request stop → disconnect → join → close CSV.
Closing the browser leaves monitoring running. A forced OS kill bypasses cleanup.

Start with [README](../README.md), [quick start](../docs/quickstart.md),
[API layout](../docs/api.md), [examples](../README.md#five-examples) and
[troubleshooting](../docs/troubleshooting.md). The source archive includes these
guides; the wheel installs the application. After changing source, reinstall it.

## Protocol source and physical validation

Available source: official Tark-hosted **MRC150/300 User Manual Rev 13**, 16 pages,
SHA-256 `24a64ef551f3e209addfb133a085c353f046ca20421d9f7453b8c9f16f4ca4eb`.
Page 12 mentions RS232/RS485 and delegates details to the separate controller
manual; pages 4/7 require actual variant confirmation. Page 7 supports the default
distilled-water 2–40 °C software policy. No authoritative communication manual or
identified physical unit is available in the exposed inputs. The unavailable
original attachment has not been represented as read.

**No physical stage was attempted.** There is no working hardware CLI, device
calibration result, verified electrical interface or serial safety telemetry.
The [hardware guide](../docs/hardware.md) cites manual facts and gives all required
serial fields. Installing serial support does not implement a protocol.

To resume:

1. Supply the matching controller manual, chiller suffix, controller model/firmware,
   interface/wiring, known serial settings and installed coolant information.
   Resolve the water-range source discrepancy for this unit (EXT-003/E017).
2. Extract baud/parity/data/stop bits, flow control/RTS/DTR, RS485 addressing,
   command syntax/registers, framing/terminators, temperature/target reads and
   target write, acknowledgements/errors, checksum/CRC, units/scaling, correlation,
   timing and side effects. Cite every command; implement only in protocol.py and
   add source-derived byte fixtures. Example 06 currently refuses connection.
3. Rerun the [exact development checks](../development/README.md#run-the-checks).
   Record candidate revision, configuration and exact first transactions for the
   physical review gate. Hardware and a safe setup must actually be available.
4. After candidate authorization, validate **interface/status → repeated temperature
   reads/front-panel comparison → existing setpoint read/front-panel comparison**.
   Record raw replies, reference readings, units, timeouts and approved disconnection
   handling. No setpoint change belongs in these read-only stages.
5. Only after explicit write authorization, confirm the coolant profile/range,
   choose one small change inside the already-safe region, send one target, read
   it back and observe response. Restore the original target only if appropriate
   and authorized. Never test extremes or bypass limits. Finish with ordered
   software cleanup and the lab's separate equipment shutdown procedure.

An exact executable physical command cannot be supplied until the documented
codec and unit configuration exist. This is the remaining external dependency;
it does not prevent installation or simulator use.

## Known limits

Windows/Python 3.12 was exercised; other platforms and physical RS485 are untested.
Sampling is not hard real time; readings are sequential. CSV flush is not durable
against power loss. Uncooperative OS/backend calls and forced process termination
cannot be guaranteed to clean up. One process owns each device. Software cannot
detect coolant presence, flow, leaks or level; disconnect does not power off a chiller.
