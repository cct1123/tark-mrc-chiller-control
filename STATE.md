# Engineering state

## Status

**SOFTWARE_DEVELOPMENT — initialization/design milestone complete**, 2026-09-10.
Full project incomplete; no hardware-ready candidate or physical validation.
Source identity: [SHA-256 manifest](outputs/source-manifest.sha256).

## Objective

Safe common API for simulator and Tark MRC150/300, independent monitoring, CSV and
Dash. Full requirement/acceptance matrix: [PROJECT.md](PROJECT.md).

## Requirements status

PASS means the stated software criterion is demonstrated at this milestone.
Requirements including real-hardware behavior remain BLOCKED despite passing mocks.

| ID / source | Short criterion | Validation | Status | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 / PROJECT | Persistent workspace/prompt record | TEST-001 | PASS | [E001](records/RECORDS.md#e001), [E008](records/RECORDS.md#e008) |
| REQ-002 / PROJECT | Common API for both devices | TEST-002/012 | BLOCKED: real unit; software PASS | [E005](records/RECORDS.md#e005), EXT-001/003 |
| REQ-003 / PROJECT | Safe default setpoint guard | TEST-003 | PASS | [E002](records/RECORDS.md#e002), [E005](records/RECORDS.md#e005) |
| REQ-004 / PROJECT | Explicit coolant profiles | TEST-003 | PASS | [E005](records/RECORDS.md#e005) |
| REQ-005 / PROJECT | No guessed protocol; fail before open | TEST-004 | PASS | [D001](records/RECORDS.md#d001), [E005](records/RECORDS.md#e005) |
| REQ-006 / PROJECT | RS232/RS485 abstraction | TEST-005/012 | BLOCKED: real interface; software PASS | [E005](records/RECORDS.md#e005), EXT-001/003 |
| REQ-007 / PROJECT | Typed faults/serialized lifecycle | TEST-002/005/006 | PASS (software) | [E004](records/RECORDS.md#e004), [E005](records/RECORDS.md#e005) |
| REQ-008 / PROJECT | Acquisition independent from Dash | TEST-006 | PASS | [E005](records/RECORDS.md#e005) |
| REQ-009 / PROJECT | CSV units/time/errors/I/O failure | TEST-007 | PASS | [E005](records/RECORDS.md#e005) |
| REQ-010 / PROJECT | Bounded history/honest freshness | TEST-006/008 | PASS | [E004](records/RECORDS.md#e004), [E005](records/RECORDS.md#e005) |
| REQ-011 / PROJECT | Dash status/plot/control | TEST-008/011 | PASS (simulator) | [E005](records/RECORDS.md#e005), [E006](records/RECORDS.md#e006) |
| REQ-012 / PROJECT | Deterministic simulator/integration | TEST-002/009 | PASS | [E005](records/RECORDS.md#e005) |
| REQ-013 / PROJECT | No invented safety telemetry | TEST-008/011 | PASS | [E002](records/RECORDS.md#e002), [E006](records/RECORDS.md#e006) |
| REQ-014 / PROJECT | Reproducible package/tests | TEST-010 | PASS | [E007](records/RECORDS.md#e007) |
| REQ-015 / PROJECT | Identified-unit physical acceptance | TEST-012 | BLOCKED | EXT-001/003; no physical test |

## Current system

[D001](records/RECORDS.md#d001): Chiller → device contract → simulator OR serial
device/codec/transport. One monitor worker writes CSV/bounded live state; Dash
reads snapshots and submits validated setpoints. Wire semantics belong in
[protocol.py](src/tark_chiller/protocol.py). MissingProtocol prevents port opening.
RS232/RS485 are generic adapters requiring explicit settings, tested using fakes.

Entry points: [README](README.md), [package](src/tark_chiller/__init__.py),
[CLI](src/tark_chiller/__main__.py), [tests](tests), [report](outputs/REPORT.md).
Engineering directories: src, tests, records, outputs. Local venv/build output ignored.

## Working / validated

133 tests pass: 50 core, 52 serial fakes, 21 monitoring/CSV/CLI, 10 GUI.
Ruff, formatting, dependency check, sdist/wheel and wheel-only smoke pass.
Browser: safe input applied, invalid input rejected, live curves and refresh
preserve session. Evidence: E005–E007 and [E009 review/regression](records/RECORDS.md#e009).

## Current gaps and known failures

- No known failing automated test. Three lifecycle/freshness review findings fixed
  and revalidated. No physical reliability claim follows.
- Sustained acquisition with concurrent GUI activity and combined injected faults
  is not characterized; current checks are short component/integration tests.
- Codec, units negotiation, acknowledgements/response correlation, startup
  semantics, actual electrical compatibility and safety telemetry are unknown.
- No physical accuracy, calibration or thermal-performance validation.
- GUI input outside HTML bounds currently yields a numeric-validation message
  because Dash supplies None; the write is correctly refused.

## Current configuration

Windows; project .venv Python 3.12.14; Dash 4.4.1, Plotly 6.9.0, pySerial 3.5,
pytest 9.1.1, Ruff 0.16.7. [Exact dependencies](requirements-tested.txt).
Simulator: initial/target 20 °C, 30 s time constant, uncalibrated.
Default polling/GUI refresh 1 s; history 3,600 samples; stale threshold 3 s (CLI
raises it for slow polling); CSV new file per run, each row flushed.
Transport budgets: 1 s per transaction and 4,096 response bytes, configurable.
Actual serial settings remain unspecified; budgets are software choices.
No hardware accessed. Preview stopped; no pending helper/process.
Reviewed initial commit prepared for origin/main per prompt 3; source hashes
identify the implementation independently of commit metadata. Use Git history
and remote tracking for the resulting commit/push status.

Authority: prompt 2 authorizes autonomous local software work and specialists.
No hardware integration approval recorded. Template candidate review remains
applicable after meaningful independent work and protocol implementation.

## Current priority and precise next engineering action

Add a repeatable **sustained simulator/fake-serial acceptance run**: 10 minutes at
1 s polling with 120-sample history; interleave Dash refresh/setpoint callbacks;
inject one disconnect/reconnect and one malformed response in fake serial, with
CSV failure in a separate run. Record interval distribution, maximum history
length, error visibility, no write replay and clean shutdown. Diagnose failures;
append E010 (next free ID after E009), update requirement statuses.
This independent action needs neither protocol information nor hardware.

## Blockers / external dependencies

- **EXT-001:** matching controller-manufacturer communication manual and exact
  controller identity/firmware absent. Search covered exposed project inputs and
  bounded official public sources. Need commands/registers, baud/framing,
  terminators, addresses, checksums, responses/acknowledgements, units/scaling,
  timing, startup/read side effects and pinout/mode. Clear with authoritative
  matching documentation; then implement local codec and golden-vector tests.
  Affects physical REQ-002/006 and REQ-015.
- **EXT-002:** referenced attachment not exposed. Official online Rev 13 used
  instead (E002); compare supplied revision when available.
- **EXT-003:** actual MRC suffix/interface, installed coolant, wiring, physical
  setup and calibrated reference unavailable. RS485 is unit-dependent given the
  manual inconsistency. Blocks physical acceptance, not software development.

## Human action required

None for this milestone or next software action. Future codec work needs EXT-001;
a hardware access/approval request would be premature now.

## Completion status

Requested initialization/design milestone is internally consistent and tested.
Full project is **not hardware-ready, fully validated or production-ready**.
Resume from the next action; keep physical requirements outstanding.
