# Engineering state

## Status

**Software candidate 0.2.0 complete; protocol and physical validation BLOCKED.**
Prompt 12's compact-driver design supersedes the 0.1 internal API. Six functional
modules plus two entry files replace thirteen files; package Python lines fall
from 2,056 to 1,249 (39.3%). Current scope and acceptance are in [PROJECT](PROJECT.md),
with the merge decision in [D006](records/RECORDS.md#d006).

No physical discovery, serial opening or device operation was performed.
This is neither HARDWARE_READY nor physically VALIDATED. Resume phase:
SOFTWARE_DEVELOPMENT when the matching communication manual arrives.
The [prompt log](prompt%20log.md) includes prompt 12 verbatim.

## Requirements status

All PASS entries below use the current 0.2 software evidence in E030. Earlier
E028 results are historical, including removed CSV append and simulator options.

| ID | Short criterion | Method | Current result | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 | Workspace, prompts and traceability | TEST-001 | PASS | E029/E030, D006 |
| REQ-002 | Common synchronous Chiller API | TEST-002/012 | Software PASS; physical BLOCKED | E030; EXT-001/003 |
| REQ-003 | 2–40 °C validation before backend access | TEST-003 | PASS | Controller, serial and GUI adversarial tests |
| REQ-004 | Explicit coolant bounds and source | TEST-003 | PASS | Controller configuration tests |
| REQ-005 | Missing protocol prevents port creation | TEST-004/019 | PASS | Serial and hardware-template tests |
| REQ-006 | Explicit RS232/RS485 backend | TEST-005/012 | Software PASS; physical BLOCKED | Serial fixtures; EXT-001/003 |
| REQ-007 | Serialized I/O, faults and no stale write | TEST-002/005/014 | PASS | Controller and serial fault tests |
| REQ-008 | Optional single monitor and bounded stop | TEST-006/019 | PASS | Monitor and interruption tests |
| REQ-009 | CSV time, units, missing values and errors | TEST-007/019 | PASS | Monitor/example disk-fault tests |
| REQ-010 | Bounded snapshots and honest freshness | TEST-006/008 | PASS | Monitor and GUI tests |
| REQ-011 | Thin Dash display and safe setpoint callback | TEST-008/011 | PASS | GUI HTTP tests and browser review |
| REQ-012 | Simulator substitution; dependency-free core | TEST-002/009/019 | PASS | Import isolation and integration tests |
| REQ-013 | No invented physical safety telemetry | TEST-008/011 | PASS | Manual/source/GUI review |
| REQ-014 | Reproducible typed package and dependencies | TEST-010/019 | PASS | Fresh non-editable install and package checks |
| REQ-015 | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |
| REQ-016 | Deterministic simulator and synthetic faults | TEST-013 | PASS | Controller/serial/end-to-end tests |
| REQ-017 | Finite read recovery, no write replay | TEST-014 | PASS | Serial fault and queued-write tests |
| REQ-018 | Exclusive new CSV; no overwrite | TEST-015/019 | PASS | Monitor and example repeat-run tests; D006 |
| REQ-019 | Sustained independent whole-stack operation | TEST-016 | PASS | E030: 600 s / 18,445 rows / 14,730 concurrent callbacks; fault integration |
| REQ-020 | Tested guides, examples and actual visuals | TEST-017 | PASS | Example tests, links and browser review |
| REQ-021 | Bootstrap client and researcher workflow | TEST-018 | PASS | GUI tests, screenshot and five SVGs |
| REQ-022 | Install/CLI/termination/hardware template | TEST-019 | PASS | Fresh installation, signals and example tests |
| REQ-023 | Compact reusable driver, explicit lifecycle | TEST-020 | PASS | Module inventory, import and multi-controller tests |

## Implementation and current evidence

[Architecture](development/architecture.md): Chiller owns one backend and optionally
one monitor. Applications use synchronous read/set functions; monitor start is
explicit. Disconnect cancels recovery, closes serialized device I/O and joins the
worker; the worker closes its CSV. Dash reads snapshots and calls the same public
setpoint method. The serial backend contains the codec boundary and bounded byte
exchange. No global registry, compatibility shim or separate logger/state lifecycle
remains. Do not share a raw backend across controllers or processes.

- 187 unit/integration/fault/example tests PASS against a non-editable installation
  in a fresh Python 3.12.14 environment; [JUnit](outputs/driver-tests.xml).
- Ruff lint/format, mypy for eight package files and pip check PASS. Zero core
  dependencies; pinned GUI/serial/development dependencies installed normally.
- Core module count and installed/source integrity: [audit](outputs/driver-audit.json).
- Sustained simulator/CSV/Dash result: [run](outputs/driver-soak.txt).
- Actual simulator screenshot, desktop/narrow layout, safe/unsafe controls and
  README/diagram rendering reviewed. [Report](outputs/REPORT.md), [E030](records/RECORDS.md#e030).

Version 0.2 intentionally removes old get_* names, public policy/monitor/logger
constructors, CSV append, and simulator noise/fault configuration. See D006 for
the explicit criterion changes. Safety bounds, timeout recovery and no write replay
remain. The simulator thermal model is uncalibrated; polling is not hard real time.
Windows was tested; other OS/Python versions and native electrical RS485 behavior
remain unvalidated. An uncooperative OS/backend call or forced process kill cannot
guarantee timely cleanup. Software cannot detect installed coolant, flow or leaks.

## Remaining dependencies and exact next action

- **EXT-001:** obtain the controller communication manual matching the actual unit,
  plus model/suffix, controller identity and firmware. Required information: baud,
  parity, data/stop bits, flow control, RTS/DTR, RS485 addressing/direction,
  commands/registers, framing/terminators, temperature/setpoint reads, setpoint
  write, acknowledgements/errors, checksum/CRC, units/scaling, response identity,
  timing and side effects.
- **EXT-003:** physical acceptance later requires that identified unit/interface,
  verified wiring, installed coolant, safe lab setup, reference instrument and
  candidate authorization. Resolve Rev 13's table/product-page water guidance for
  the actual unit before use (E017).
- **EXT-002, provenance only:** original attachment unavailable. Official Tark-hosted
  Rev 13 is the provisional source; compare revisions if the attachment arrives.

Next action: verify manual applicability, implement only documented codec behavior
inside serial.py, and add page-cited byte fixtures. Rerun software acceptance and
prepare exact first transactions for candidate review. Then perform approved
read-only interface/temperature/setpoint validation; one small setpoint change
requires explicit write authorization. The [hardware guide](docs/hardware.md)
gives the staged procedure. No physical authorization is requested now.
