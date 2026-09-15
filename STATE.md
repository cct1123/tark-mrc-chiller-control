# Engineering state

## Status

**Software complete — version 0.3.0; hardware acceptance BLOCKED by EXT-001/003.**
All hardware-independent requirements have current evidence. The next phase is
SOFTWARE_DEVELOPMENT when the matching controller communication manual arrives.
This is not HARDWARE_READY or a physically validated release. No physical port
was enumerated or opened and no device operation was performed.

[PROJECT](PROJECT.md) defines acceptance. [D008/E035/E036](records/RECORDS.md#d008)
record the consolidation and validation; [REPORT](outputs/REPORT.md) gives operation
and resumption instructions. [E037](records/RECORDS.md#e037) verifies the initial
README rewrite; [E038](records/RECORDS.md#e038) verifies the hardware-first revision
requested in [prompt 17](prompt%20log.md#prompt-17).
[E039](records/RECORDS.md#e039) records the final review and fixes authorized for
commit/push in [prompt 18](prompt%20log.md#prompt-18).

## Requirements status

E036 supersedes older software PASS results for the current implementation.
E037 adds documentation, fresh Quick Start and regression checks against unchanged code.
E038 supersedes the README organization and rechecks the hardware example workflows.
E039 verifies the final documentation and unchanged implementation before publication.

| ID | Short criterion | Method | Current result | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 | Workspace, prompts and traceability | TEST-001 | PASS | D008/E035/E036/E039; current links checked |
| REQ-002 | Common synchronous Chiller API | TEST-002/012 | Software PASS; physical BLOCKED | Controller and fake serial tests; EXT-001/003 |
| REQ-003 | 2–40 °C validation before backend access | TEST-003 | PASS | Controller/serial/GUI/example adversarial tests |
| REQ-004 | Explicit coolant bounds and source | TEST-003 | PASS | Configuration rejection and boundary tests |
| REQ-005 | Missing protocol prevents port creation | TEST-004/019 | PASS | Missing-codec and unconfigured lab commands |
| REQ-006 | Explicit RS232/RS485 backend | TEST-005/012 | Software PASS; physical BLOCKED | Production serial path with fixtures; EXT-001/003 |
| REQ-007 | Serialized I/O, faults and no stale write | TEST-002/005/014 | PASS | Fault/race tests and independent codec instances |
| REQ-008 | Optional single monitor and bounded stop | TEST-006/019 | PASS | Duplicate starts, fixed interval, pending reads and shutdown |
| REQ-009 | CSV time, units, missing values and errors | TEST-007/019 | PASS | CSV/disk faults; either failed read blanks both readings |
| REQ-010 | Bounded snapshots and honest freshness | TEST-006/008 | PASS | Coherent snapshots, stale display and concurrent callbacks |
| REQ-011 | Thin Dash client and validated writes | TEST-008/011 | PASS | Actual Dash HTTP/callback tests and snapshot-only refresh |
| REQ-012 | Simulator substitution; dependency-free core | TEST-002/009/019 | PASS | Import isolation, explicit lifecycle and integrated simulation |
| REQ-013 | No invented safety telemetry | TEST-008/011 | PASS | Manual/source/UI review |
| REQ-014 | Reproducible typed package | TEST-010/019 | PASS | Fresh normal install, static checks and archive verification |
| REQ-015 | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |
| REQ-016 | Deterministic simulator and synthetic faults | TEST-013 | PASS | Controller, serial and end-to-end tests |
| REQ-017 | Finite read recovery, no write replay | TEST-014 | PASS | Exhaustion/cancellation, malformed replies and uncertain writes |
| REQ-018 | Exclusive CSV, no overwrite | TEST-015/019 | PASS | Existing-file rejection and writer ownership tests |
| REQ-019 | Sustained whole-stack operation | TEST-016 | PASS | 60 s, 1,732 rows, 1,527 callbacks, clean shutdown |
| REQ-020 | Hardware-oriented guides and examples | TEST-017 | Software PASS; physical execution BLOCKED | E036–E039: lab workflows, Python blocks, guide links and visuals; EXT-001/003 |
| REQ-021 | Bootstrap client and researcher workflow | TEST-018 | PASS | GUI tests, one lab script, current links and two SVGs |
| REQ-022 | Install, lifecycle and explicit lab configuration | TEST-019 | PASS | Installed/archive suites, signals, physical-backend fixtures |
| REQ-023 | Compact reusable driver | TEST-020 | PASS | 8→6 modules, 13→11 classes, 1,311→1,220 lines; no compatibility shims |

## Current evidence

- **218 tests PASS** against a fresh, non-editable Python 3.12.14 installation:
  [JUnit](outputs/tests.xml). The extracted source archive also passes all 218
  against the installed wheel: [archive JUnit](outputs/archive-tests.xml).
- Ruff lint/format, mypy (six modules), pip check, source/wheel build and installed
  file integrity PASS. [Audit](outputs/validation.json), [build](outputs/build.txt).
- [Sustained simulator/CSV/Dash run](outputs/soak.txt): 60 seconds, 1,732 samples,
  no failed polls, history capped at 25 and a clean stop. This is software evidence.
- One hardware lab script replaces six example files. It exercises the production
  serial path through test-only endpoints; no simulator fallback or guessed wire bytes.
  All five workflows, invalid arguments, safe writes/readback, CSV faults and cleanup
  are covered. README/API Python blocks execute through that same path.
- The independent final review found no consequential migration defect or useful
  further pruning. The revised architecture SVG was inspected in a browser.
  The unchanged GUI screenshot is explicitly labeled as simulator data.
- **README review, 2026-09-15 (E037):** all audited implementation/configuration/test
  hashes still match E036. Fresh Python 3.12.14 Quick Start install and five-second
  simulator CSV run PASS; existing 218-test suite PASS in 16.37 s. README links,
  screenshot and rendered Mermaid checked. No hardware interaction occurred.
- **Hardware-first README, 2026-09-15 (E038):** install/configuration, connection
  verification, recording, approved writes and the physical-backend dashboard now
  lead; simulator demo is optional near the bottom. All 30 example/documentation
  tests PASS in 10.34 s with fake serial endpoints; 15 links/images checked.
- **Final documentation review, 2026-09-15 (E039):** fixed the dashboard backlink,
  clarified reinstalling driver changes and one program per physical unit, repaired
  the requirements table and refreshed the report. All 218 tests PASS in 15.86 s;
  Ruff lint/format, mypy and pip check PASS. Hardware status is unchanged.

Chiller owns its connection and optional worker. Monitoring derives running state
from the thread and publishes bounded immutable snapshots. The codec returns
status text; SerialDevice supplies connection metadata. Shared validation, required
locks, cancellation and finite recovery remain. Core dependencies remain empty.
Historical evidence remains available through committed Git links in records.

## Human action required / exact next action

**Obtain the communication manual matching the actual unit/controller/firmware
(EXT-001).** It must establish baud, parity, data/stop bits, flow control, RTS/DTR,
RS485 addressing/direction, command syntax/registers, framing/terminators,
temperature/setpoint reads, setpoint write, acknowledgements/errors, checksum/CRC
if used, units/scaling/resolution, response identity, timing and side effects.
Return the authoritative source and identified model/suffix/controller/firmware.

Then implement only documented codec behavior in serial.py with page-cited byte
fixtures; configure examples/lab.py and rerun software acceptance. Before any port
opening, obtain review of the concrete physical candidate and setup (EXT-003):
interface/wiring, coolant, safe operating region and reference instrument. Follow
the [staged hardware procedure](docs/hardware.md). Writes require explicit approval.
No physical authorization is requested now.

EXT-002 concerns provenance only: the original attachment is unavailable; the
cached official Rev 13 PDF matches E002's hash. Resolve the manual/product-page
water guidance for the actual unit before operation (E017). Other OS/Python versions,
electrical RS485 behavior and physical temperature performance remain unvalidated.
Polling is not real time. Forced termination and uncooperative OS calls can defeat
cleanup. The full suites emit one upstream Plotly deprecation warning each; this
application uses no map trace and no check fails.
