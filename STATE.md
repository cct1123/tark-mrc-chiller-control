# Engineering state

## Status

**Software release candidate complete. Protocol/physical phase BLOCKED.**
Prompt 10's hardware-independent requirements pass on the current source; this
is not HARDWARE_READY or a physically VALIDATED release. Resume phase:
SOFTWARE_DEVELOPMENT when the matching communication manual arrives.
No physical discovery, port opening or device operation was performed.

Package version: 0.1.0. Source baseline: fb369f6; the reviewed candidate is
identified by the [40-file manifest](outputs/release-source-manifest.sha256).
Manifest text uses the repository's canonical LF line endings.
The [prompt log](prompt%20log.md) contains all ten requests. Criteria are in
[PROJECT](PROJECT.md); methods, repairs and evidence are in
[E027/E028 and D005](records/RECORDS.md#e027). Historical PASS results were
re-audited; affected code was retested after four interruption defects were fixed.

## Requirements status

| ID | Short criterion | Method | Current result | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 | Workspace, prompts and traceability | TEST-001 | PASS | E027/E028, D005 |
| REQ-002 | Common device API | TEST-002/012 | Software PASS; physical BLOCKED | E028; EXT-001/003 |
| REQ-003 | Default 2–40 °C guard before writes | TEST-003 | PASS | E028 |
| REQ-004 | Explicit coolant bounds and source | TEST-003 | PASS | E028 |
| REQ-005 | Missing protocol refuses before port open | TEST-004/019 | PASS | E028 |
| REQ-006 | RS232/RS485 transport | TEST-005/012 | Software PASS; physical BLOCKED | E028; EXT-001/003 |
| REQ-007 | Typed faults, serialized lifecycle, no stale writes | TEST-002/005/014/019 | PASS | E027/E028 |
| REQ-008 | Independent acquisition, one owner, bounded stop | TEST-006/019 | PASS | E027/E028 |
| REQ-009 | CSV units/time/errors and failure isolation | TEST-007/019 | PASS | E027/E028 |
| REQ-010 | Bounded atomic history and honest freshness | TEST-006/008 | PASS | E028 |
| REQ-011 | Dash history, status and safe controls | TEST-008/011 | PASS | E028 |
| REQ-012 | Simulator substitution, GUI-independent core | TEST-002/009/019 | PASS | E028 |
| REQ-013 | No invented safety telemetry | TEST-008/011 | PASS | E027/E028 |
| REQ-014 | Typed package and reproducible dependencies | TEST-010/019 | PASS | E028; full fresh installation |
| REQ-015 | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |
| REQ-016 | Repeatable simulator and synthetic serial faults | TEST-013 | PASS | E028 |
| REQ-017 | Finite cancellable recovery, no write replay | TEST-014 | PASS | E028 |
| REQ-018 | Validated CSV append, sessions, writer exclusion | TEST-015/019 | PASS | E027/E028 |
| REQ-019 | Sustained concurrent whole-stack operation | TEST-016 | PASS | E028 |
| REQ-020 | Tested researcher guides, examples and visuals | TEST-017 | PASS | E028 |
| REQ-021 | Bootstrap dashboard and simpler researcher workflow | TEST-018 | PASS | E026/E028; GUI source/styles unchanged |
| REQ-022 | Installable release, CLI lifecycle and hardware template | TEST-019 | PASS | E027/E028, D005 |

## Implementation and evidence

Chiller has seven device operations. Monitor owns sampling and bounded shared
history; CsvLogger owns one file. Dash reads snapshots and submits controls
through that same Chiller. Simulator and SerialDevice share the device contract;
SerialDevice composes a codec and RS232/RS485 transport. MissingProtocol blocks
before endpoint creation. No serial syntax/settings were invented.
[Architecture](development/architecture.md), [researcher entry point](README.md).

One coolant validator guards API/device writes. Default distilled-water bounds
are 2–40 °C; custom profiles require a documented source. Connection changes
cancel queued writes, and uncertain writes are never replayed. Read recovery is
off by default and bounded when enabled. Interrupted startup preserves ownership;
serial interruption closes its endpoint; interrupted CSV writes refuse reuse;
fatal worker errors remain visible. Shutdown requests stop, disconnects, joins,
then closes CSV. A timed-out stop retains ownership until polling finishes.

- **401 tests PASS, 27.55 s**, against a non-editable installed wheel in a fully
  fresh Python 3.12.14 environment with all [43 pinned dependencies](requirements-tested.txt).
  No shared third-party paths. [JUnit](outputs/release-tests.xml).
- Ruff lint/format, mypy (13 modules), pip check, source archive/wheel build,
  normal source installation, module/console examples and package integrity PASS.
  All 17 package files match source, wheel and installation.
  [Package evidence](outputs/release-package.json), [build](outputs/release-build.txt).
- **600 s fault run PASS:** 596 sample/CSV rows, 120 retained history, 31 failed
  polls, 4,002 refreshes, 161 reloads and 401 unsafe requests rejected. Exactly
  three applied writes and seven planned opens; no replay. Final valid temperature
  18.014839 °C at an 18 °C target. [Summary](outputs/release-soak.json).
- Actual installed simulator GUI: safe/unsafe requests, disconnect/reconnect,
  reload and Ctrl+C shutdown PASS. 360 complete CSV rows, 51 unavailable rows,
  37 rows after tab closure. CSV lock released; ports 8050/8051 closed.
  [Operations](outputs/release-operations.json). Screenshot and tour refreshed.
- Current guides, links, illustrations, README Python and six example entry
  points checked. Example 06 intentionally reports missing protocol without
  opening a port. Final independent review found no consequential software gap.

Windows was exercised. Other OS/Python versions, electrical RS485 operation,
calibration and physical reliability remain unvalidated. Scheduling is not hard
real time; CSV flush is not power-loss durability. Forced process termination
and uncooperative OS/backend calls cannot guarantee cleanup. Software cannot
detect installed coolant, leaks, flow or level. One process owns each device.

## Remaining dependencies and exact next action

- **EXT-001:** obtain the communication manual matching the actual controller,
  plus chiller model/suffix, controller model and firmware. Extract baud/parity/
  data/stop bits, flow control, RTS/DTR, RS485 addressing/direction, command/register
  syntax, framing/terminators, temperature/target reads, target write, responses/
  errors, checksum/CRC, units/scaling, response identity, timing and side effects.
- **EXT-003:** later physical acceptance requires the identified unit/interface,
  verified wiring, installed coolant, reference instrument, safe lab setup and
  candidate authorization. Resolve Rev 13's water table versus product-page
  guidance for the actual unit before physical use (E017).
- **EXT-002, provenance only:** the original attachment remains unavailable.
  Official Tark-hosted Rev 13 was used; compare revisions if supplied later.

Next engineering action: verify manual applicability, implement only documented
commands in protocol.py with page-cited byte fixtures and explicit configuration,
and rerun software acceptance. Prepare the exact first transactions for candidate
review. Then validate interface, temperature and setpoint reads; perform one small
safe write/readback only after explicit write authorization. The
[hardware guide](docs/hardware.md) and [report](outputs/REPORT.md) give commands,
evidence to record and shutdown order. No physical authorization is requested now.
