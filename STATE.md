# Engineering state

## Status

**Documentation complete; overall project BLOCKED on protocol/physical dependencies**,
2026-09-10. All hardware-independent requirements, including lab-user guides and
visuals (REQ-020), are PASS. Commands/examples ran in a fresh Python 3.12 environment;
the actual GUI screenshot, diagrams and rendered guides were inspected (E022/E023).
No physical validation occurred or is authorized by this phase. Resume
SOFTWARE_DEVELOPMENT when the matching protocol document arrives. This is not
HARDWARE_READY or physically VALIDATED.

Software baseline: 0a45586. Prompt 8's language review is complete and authorizes
publishing the documentation to origin/main. E024 records the review and checks;
Git HEAD and origin/main identify the resulting revision and push status.
Application code, tests and dependencies are unchanged. [Prompt log](prompt%20log.md)
preserves all eight requests.
[PROJECT.md](PROJECT.md) holds the full acceptance criteria.

## Requirements status

Software PASS rows refer to E018, confirmed by E022 and E024's 349-test runs.
Documentation acceptance refers to E022–E024.
TEST methods and E records resolve in [records](records/RECORDS.md).

| ID / source | Short criterion | Validation | Status | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 / PROJECT | Workspace, prompts, requirement traceability | TEST-001 | PASS | E020–E024, D003 |
| REQ-002 / PROJECT | Common API for both backends | TEST-002/012 | Software PASS; physical BLOCKED | E018; EXT-001/003 |
| REQ-003 / PROJECT | Default 2–40 °C guard before writes | TEST-003 | PASS | E002, E017, E018 |
| REQ-004 / PROJECT | Explicit coolant bounds/provenance | TEST-003 | PASS | E018 |
| REQ-005 / PROJECT | Missing codec refuses before port open | TEST-004 | PASS | E018 |
| REQ-006 / PROJECT | RS232/RS485 transport abstraction | TEST-005/012 | Software PASS; physical BLOCKED | E018; EXT-001/003 |
| REQ-007 / PROJECT | Typed faults, serialized lifecycle, no stale writes | TEST-002/005/014 | PASS | E018 |
| REQ-008 / PROJECT | Independent single-owner acquisition and stop | TEST-006 | PASS | E018 |
| REQ-009 / PROJECT | CSV units/time/errors and failure isolation | TEST-007 | PASS | E018 |
| REQ-010 / PROJECT | Bounded atomic history/counters and honest freshness | TEST-006/008 | PASS | E018 |
| REQ-011 / PROJECT | Dash plot, status and safe controls | TEST-008/011 | PASS | E018, E019 |
| REQ-012 / PROJECT | Simulator substitution and GUI-independent core | TEST-002/009 | PASS | E018 |
| REQ-013 / PROJECT | No invented physical safety telemetry | TEST-008/011 | PASS | E002, E018 |
| REQ-014 / PROJECT | Reproducible typed package | TEST-010 | PASS | E018 |
| REQ-015 / PROJECT | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |
| REQ-016 / PROJECT | Deterministic thermal/fault simulation and fake protocol | TEST-013 | PASS | E018 |
| REQ-017 / PROJECT | Finite cancellable read recovery, no write replay | TEST-014 | PASS | E018 |
| REQ-018 / PROJECT | Validated CSV append, sessions and writer exclusion | TEST-015 | PASS | E018 |
| REQ-019 / PROJECT | Sustained concurrent whole-stack operation | TEST-016 | PASS | E018, E019 |
| REQ-020 / PROJECT | Lab-user guides, tested examples and visuals | TEST-017 | PASS | E022–E024 |

## Current implementation

[ARCHITECTURE.md](ARCHITECTURE.md) describes the modules and their responsibilities;
[D003](records/RECORDS.md#d003) records the review decisions. Controls and monitoring
share one Chiller instance, which allows one device operation at a time. The
simulator and SerialDevice use the same interface. SerialDevice uses a protocol
codec and serial transport; the default MissingProtocol blocks port opening.
Dash reads shared samples. Only one monitor can run per Chiller/LiveState, and
only one CsvLogger can write to each file.

The API and device use the same setpoint validator. Default water limits are
2–40 °C; other profiles need a documented source. A waiting write is cancelled if
a newer connect/disconnect action occurs. Uncertain writes are never repeated.
Automatic read recovery is off by default; when enabled, it has an attempt limit
and stops on protocol errors. Shutdown stops monitoring, disconnects the device,
waits for active polling to finish, then closes the CSV.

Python 3.12.14 / Windows; [exact 42-package environment](requirements-tested.txt).
Core runtime has no external dependency. Software defaults: simulator 20 °C /
30 s time constant / no noise; 1 s polling/refresh; 3,600 history; 3 s stale age;
generic serial budget 1 s / 4,096 bytes. These are not physical controller facts.

## Current evidence and limitations

- Fresh .venv-review: 349 tests PASS in 9.82 s, Ruff lint/format PASS (24 files),
  mypy PASS (14 modules), pip check PASS. [JUnit](outputs/review-tests.xml).
- Source archive → wheel, exact module bytes, typing marker/demo packaging and
  wheel-only simulator/both fake adapters PASS. [Build](outputs/review-build.txt).
- Three independent specialists plus cross-review; reproduced defects and
  corrections are in E017/E018.
- Final 600 s: 596 sample/CSV rows, 120 history, 30 unavailable polls, 4,007 refreshes,
  161 reloads, 401 invalid writes rejected, exactly three applied targets and seven
  planned opens; final simulated temperature 17.998355 °C / target 18 °C. Clean stop.
  [Run summary](outputs/review-soak.json). This supersedes E013's earlier result.
- Browser safe/unsafe controls, last-poll status, disconnect/reconnect and reload
  passed; 173 more rows after tab closure (E019). [Final manifest](outputs/source-manifest.sha256)
  identifies 30 source/config files; E020 records the handoff audit.
- Documentation environment: 349 tests PASS in 10.67 s, static/type/dependency/
  package checks PASS. Quick-start GUI and headless commands, both Python blocks,
  CLI/Python CSV append, rendered guides and local links PASS (E022/E023).
  [Quick start](docs/quickstart.md) is the entry point; [user guide](docs/usage.md)
  covers status, logging, API and safety. README distinguishes simulator-tested,
  fake-serial-tested and absent physical validation.
- Language review: clearer setup, defined terms and simpler CSV/error guidance;
  349 tests PASS in 9.94 s, static/type/dependency checks PASS (E024). Commands,
  Python examples and visual assets are unchanged from E022/E023.

Tests use injected memory endpoints and synthetic syntax, never a real port.
Windows file locking was exercised across processes/aliases; POSIX flock is not
executed here. No hard-real-time, calibration, physical reliability or RS485
electrical claim follows. CSV flush is not a power-loss guarantee. Reads are
sequential, and calls ignoring timeouts cannot be forcibly interrupted.
The application supports one process/device owner; it is a local simulator CLI.

## External dependencies and precise next action

- **EXT-001:** matching controller-manufacturer communication manual and controller
  model/firmware are absent after renewed input/official-source search (E017).
  Needed: baud/parity/data/stop bits, RS485 addressing, command/register syntax,
  framing/terminators, temperature/setpoint reads, setpoint write, acknowledgements /
  errors, checksum/CRC, units/scaling, response identity, timing and side effects.
- **EXT-003:** actual-unit interface, wiring, installed coolant, calibrated reference,
  physical access and candidate authorization are required for physical acceptance.
  Resolve the official product page's 5 °C coolant guidance versus the Rev 13
  2 °C table for that unit before physical use (E017).
- **EXT-002 (provenance note):** the originally mentioned attachment is not exposed.
  Official Tark-hosted Rev 13 was used provisionally (E002/E017). This is not a
  separate software blocker; compare the supplied revision when it becomes available.

Precise next engineering action: once EXT-001 arrives, verify applicability and
implement the localized codec in protocol.py with document-derived golden vectors
and explicit settings. Rerun the software suite, then prepare exact first physical
interactions for the template hardware review gate. No useful protocol
implementation can precede that source. Documentation work is complete.

## Authority and handoff

The user's authority covers local corrections, specialists and hardware-free
validation. No hardware candidate or physical authorization exists. The only
external request needed for the next engineering phase is the matching controller
manual and identity. No hardware approval is being requested at this point.
No monitor, preview, test process or pending mutation remains at handoff.
[Report](outputs/REPORT.md) summarizes findings; [README](README.md) links the lab guides.
