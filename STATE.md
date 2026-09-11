# Engineering state

## Status

**Software candidate 0.2.2 complete; protocol and physical validation BLOCKED.**
The researcher examples now target the serial backend, with no simulator fallback.
One editable lab connection file replaces the old standalone hardware template.
Core Python uses explicit names, keyword data construction and clear control flow.
The package retains six functional modules plus two entry files (1,311 Python lines).
[PROJECT](PROJECT.md) defines acceptance; [D007](records/RECORDS.md#d007) records
prompt 14's hardware-example scope. All session requests remain in the
[prompt log](prompt%20log.md).

No physical discovery, port opening or device operation was performed.
This is neither HARDWARE_READY nor physically VALIDATED. Resume phase:
SOFTWARE_DEVELOPMENT when the matching controller communication manual arrives.

## Requirements status

E033/E034 revalidate the current software. Earlier E030/E032 results apply to
0.2.0/0.2.1 respectively and remain historical evidence.

| ID | Short criterion | Method | Current result | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 | Workspace, prompts and traceability | TEST-001 | PASS | E033/E034, D007 |
| REQ-002 | Common synchronous Chiller API | TEST-002/012 | Software PASS; physical BLOCKED | E034; EXT-001/003 |
| REQ-003 | 2–40 °C validation before backend access | TEST-003 | PASS | Controller/serial/GUI/example adversarial tests |
| REQ-004 | Explicit coolant bounds and source | TEST-003 | PASS | Controller configuration tests |
| REQ-005 | Missing protocol prevents port creation | TEST-004/019 | PASS | Missing-codec and incomplete-example-configuration tests |
| REQ-006 | Explicit RS232/RS485 backend | TEST-005/012 | Software PASS; physical BLOCKED | Serial fixtures; EXT-001/003 |
| REQ-007 | Serialized I/O, faults and no stale write | TEST-002/005/014 | PASS | Controller/serial faults; per-controller example codec isolation |
| REQ-008 | Optional single monitor and bounded stop | TEST-006/019 | PASS | Fixed interval, paced worker and shutdown tests |
| REQ-009 | CSV time, units, missing values and errors | TEST-007/019 | PASS | Monitor/example disk-fault tests |
| REQ-010 | Bounded snapshots and honest freshness | TEST-006/008 | PASS | Monitor and GUI tests |
| REQ-011 | Thin Dash client and validated writes | TEST-008/011 | PASS | Real Dash HTTP tests; physical-backend example lifecycle |
| REQ-012 | Simulator substitution; dependency-free core | TEST-002/009/019 | PASS | Import isolation and integration tests |
| REQ-013 | No invented safety telemetry | TEST-008/011 | PASS | Manual/source/GUI review |
| REQ-014 | Reproducible typed package | TEST-010/019 | PASS | Fresh non-editable install, build/static checks |
| REQ-015 | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |
| REQ-016 | Deterministic simulator and synthetic faults | TEST-013 | PASS | Controller/serial/end-to-end tests |
| REQ-017 | Finite read recovery, no write replay | TEST-014 | PASS | Serial faults, queued writes, uncertain-write example |
| REQ-018 | Exclusive CSV, no overwrite | TEST-015/019 | PASS | Monitor and example repeat-run tests |
| REQ-019 | Sustained whole-stack operation | TEST-016 | PASS | 60 s, 1,763 rows, 1,527 concurrent callbacks |
| REQ-020 | Hardware-oriented guides and examples | TEST-017 | Software PASS; physical execution BLOCKED | E034; EXT-001/003 |
| REQ-021 | Bootstrap client and researcher workflow | TEST-018 | PASS | GUI, example, link and illustration checks |
| REQ-022 | Install, lifecycle and explicit lab configuration | TEST-019 | PASS | Installed suite, signals, real serial path with memory endpoints |
| REQ-023 | Compact reusable driver | TEST-020 | PASS | Eight modules, direct API, no implicit workers/dependencies |

## Current implementation and evidence

Chiller owns its backend and optional monitor. The GUI only reads snapshots and
calls the public setpoint method. Serial settings and monitoring interval are
read-only after validation. Stop/start monitoring to change its rate. CSV close
runs outside the snapshot lock; writes are never retried or replayed.

- **209 tests PASS** in a fresh Python 3.12.14 environment with a normal installation:
  [JUnit](outputs/human-installed-tests.xml).
- Ruff lint/format, mypy and pip check PASS. Source/wheel packaging and source
  archive tests are recorded in [E034](records/RECORDS.md#e034) and the
  [audit](outputs/human-review.json).
- Sustained software-only regression: [run](outputs/human-soak.txt).
- All five researcher examples exercise production SerialDevice with test-only
  endpoints. Tests verify reads, one deliberate target, mismatched/uncertain
  readback, CSV errors, Ctrl-C, Dash HTTP, independent codec state and refusal of
  incomplete configuration. No test bytes are copied into examples.
- README and API snippets use the same configured hardware path. The actual
  screenshot remains explicitly labeled simulator data. The updated hardware
  setup diagram was inspected in a browser. [Report](outputs/REPORT.md).

Only protocol exceptions are custom; normal failures use Python's standard
exceptions. No new package module, configuration framework or dependency was added.
Polling is not real time. Other OS/Python versions and electrical RS485 behavior
remain unvalidated. Forced termination or an uncooperative OS call can prevent
cleanup. One upstream Plotly deprecation warning occurred during integration;
no map trace is used and no test failed.

## Dependencies and exact next action

- **EXT-001:** obtain the communication manual matching the chiller model/suffix,
  controller identity and firmware. Required: baud, parity, data/stop bits, flow
  control, RTS/DTR, RS485 addressing/direction, commands/registers, framing and
  terminators, temperature/setpoint reads, setpoint write, acknowledgements/errors,
  checksum/CRC, units/scaling/resolution, response identity, timing and side effects.
- **EXT-003:** later physical acceptance requires the identified unit/interface,
  verified wiring, installed coolant, safe lab setup, reference instrument and
  candidate authorization. Resolve the manual/product-page water guidance for
  the actual unit (E017).
- **EXT-002, provenance only:** the original attachment is unavailable. The cached
  official Tark-hosted Rev 13 manual still matches E002's SHA-256.

Next: verify the communication manual, implement only its documented codec in
serial.py and add page-cited byte fixtures. Configure examples/connection.py with
verified settings and the implemented codec class. Rerun software acceptance,
then obtain candidate review before physical reads. A small controlled target
change requires explicit write authorization. Follow the [hardware guide](docs/hardware.md).
No physical authorization is requested now.
