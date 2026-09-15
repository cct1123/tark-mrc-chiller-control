# Engineering state

## Status

**AWAITING_HUMAN_REVIEW — version 0.4.0 CAL 3300/9300 software candidate.**
Hardware-independent work and final source review are complete for the documented
subset. No physical port was enumerated/opened; no hardware command was sent.
The installed controller is **unconfirmed**. This is not a validated MRC release.

[PROJECT](PROJECT.md) defines scope; [REPORT](outputs/REPORT.md) presents the
candidate. [E042–E044 / D009](records/RECORDS.md#e042) record sources, implementation,
tests and review. The candidate is identified by version and the normalized
source/configuration hashes in [validation.json](outputs/validation.json);
the publication commit contains those exact files.

## Requirements status

All software results below apply to v0.4.0. Physical portions remain UNTESTED.
Prompt 21 / D009 supersedes the prior class/line reduction target while preserving
six modules. Earlier v0.3 results remain historical.

| ID | Short criterion | Method | Result | Evidence |
| --- | --- | --- | --- | --- |
| REQ-001 | Traceable workspace/prompts | TEST-001 | PASS | Current checkpoint, report, E042–E044 |
| REQ-002 | Common API | TEST-002/012 | Software PASS; physical UNTESTED | 296-test suite; EXT-001/003 |
| REQ-003 | Default 2–40 °C pre-I/O guard | TEST-003 | PASS | Public adversarial/boundary tests, including CAL |
| REQ-004 | Named custom coolant/source | TEST-003 | PASS | Profile tests; CAL additionally restricts supported range |
| REQ-005 | Documented protocol; missing config refuses ports | TEST-004/021 | PASS | Published byte vector, explicit lab configuration |
| REQ-006 | RS232/RS485 transport | TEST-005/012 | Software PASS; electrical UNTESTED | Serial fixture suite; no unit RS485 claim |
| REQ-007 | Serialization, cleanup, no stale/replayed writes | TEST-002/005/014/021 | PASS | Existing races plus all five CAL write fault stages |
| REQ-008 | Optional single monitor | TEST-006/019 | PASS | Lifecycle/stop tests |
| REQ-009 | CSV units/time/errors | TEST-007/019 | PASS | CSV tests and CAL soak |
| REQ-010 | Bounded history and freshness | TEST-006/008 | PASS | Snapshot/GUI tests; 25-sample soak cap |
| REQ-011 | Thin GUI, validated writes | TEST-008/011 | PASS | HTTP callbacks, read-only rejection and browser review |
| REQ-012 | Repeatable simulator, dependency-free core | TEST-002/009/019 | PASS | Clock/model/import and integration tests |
| REQ-013 | Honest safety telemetry | TEST-008/021 | PASS | CAL display diagnostics; flow/level/leak unknown |
| REQ-014 | Reproducible package | TEST-010/019 | PASS | Fresh normal install, build, lint/types/dependency checks |
| REQ-015 | Identified-unit physical/calibration acceptance | TEST-012 | UNTESTED | Awaiting review and hardware evidence |
| REQ-016 | Synthetic and protocol simulators | TEST-013/021 | PASS | Memory-only endpoint with production frames |
| REQ-017 | Finite read recovery; no write replay | TEST-014/021 | PASS | Recovery suite and CAL uncertain-session latch |
| REQ-018 | Exclusive new CSV | TEST-015/019 | PASS | No-overwrite and fault tests |
| REQ-019 | Sustained integrated operation | TEST-016 | PASS | CAL: 60 s, 183 rows, 1,167 callbacks, clean shutdown |
| REQ-020 | Hardware-first verified guides | TEST-017 | Software PASS; physical UNTESTED | Examples, links, screenshot, Mermaid review |
| REQ-021 | Researcher workflow / dashboard | TEST-018 | PASS | One lab script, read-only controls, controller detail |
| REQ-022 | Install/configuration/normal lifecycle | TEST-019 | PASS | Both installed suites, signals, unconfigured CLI refusal |
| REQ-023 | Compact reusable six-module design | TEST-020 | PASS | Inventory and source review; D009 scope adjustment |
| REQ-024 | Source-backed CAL commands and guards | TEST-021/012 | Software PASS; physical UNTESTED | Protocol vectors, limits/identity/fault tests |
| REQ-025 | Command simulator and human test procedure | TEST-021/017 | PASS | Staging/security tests; hardware tutorial and recovery |

## Current evidence and configuration

- **296 tests PASS** in existing non-editable installation (32.06 s) and fresh
  installation (32.20 s), Windows / Python 3.12.14:
  [installed JUnit](outputs/tests.xml), [fresh JUnit](outputs/fresh-tests.xml).
- [CAL soak](outputs/soak.txt): 60 seconds, 183 valid CSV rows, 1,167 concurrent
  callbacks, history cap 25, clean stop. Accelerated synthetic thermal clock;
  neither cooling performance nor serial timing is physically measured.
- Ruff lint/format, mypy (six modules), pip check and package build PASS.
  [Audit](outputs/validation.json), [build](outputs/build.txt).
- Fresh Quick Start CAL run: five samples, simulator labels, no failed polls.
  Unconfigured lab commands refuse before endpoint creation.
- Six package modules, one lab script. Cal33xx and CalSimulator extend serial.py.
  Core dependencies remain empty. Optional monitor/CSV/Dash ownership is unchanged.
- Shipped configuration: port/address unset, writes disabled, no expected identity,
  no RS485 mode. No local equipment-specific settings are committed.
- CAL profile: documented 3300/9300 model codes 1/2/3, firmware FFFF/1/2,
  RTD and Celsius. Read-only discovery within that profile; writes require exact
  reviewed codes, scale/lock/mode/resolution checks and successful readback.
- An interrupted write may leave program mode active or a target staged/saved.
  No retry/cleanup commit. The same backend refuses reconnection until replaced
  after human inspection; object replacement alone is not authorization.

## Human action required / next phase

**Identify the actual controller and review this candidate with its physical setup
before performing the read-only TEST-012 procedure.**

1. Known: the supplied 18-page MRC manual is reviewed and the official CAL protocol
   subset passes software validation. Actual MRC/controller identity, wiring and
   installed coolant are not available. The identity question sent during this
   session has no reply yet.
2. Boundary: software-side review is complete; useful next integration work needs
   actual equipment facts and the AGENTS.md candidate review gate.
3. Return: full MRC model/suffix, controller model/firmware and comms option;
   approved cable/adapter/port/serial settings, coolant/concentration and approved
   °C bounds; candidate-specific approval for the first read-only tests.
4. Human test: follow [hardware.md](docs/hardware.md#human-hardware-test), record
   identity codes, panel/reference comparisons in °C, reference calibration and
   lab-approved tolerance. Writes require a separate approved target and recovery
   scope including CAL's save/restart side effect.
5. Next: compare human results with the documented profile; resolve discrepancies
   before any write. Record TEST-012 evidence before claiming physical validation.

EXT-001: actual-unit controller/interface/settings identity remains pending; the
protocol-document portion is resolved for the CAL subset.
EXT-002: original attachment access **resolved** by prompt 21; its SHA256 and
different 18-page pagination are recorded in E042.
EXT-003: candidate/setup approval, actual wiring/coolant and physical validation
remain pending. No prior general permission is treated as hardware approval.

## Limits

Negative wire encoding, Fahrenheit, linear inputs and other CAL models are
unsupported. The simulator omits real restart delay, electrical behavior and
fluid/PID physics. Verify DISP/SP.LK and timing on the actual controller.
RTU has no transaction IDs; a valid delayed reply cannot always be identified.
Polling is not real time; OS calls and forced termination can defeat cleanup.
Resolve the manufacturer's 2 °C versus 5 °C water guidance for the actual unit.
Upstream Plotly scattermapbox deprecation warnings do not fail the checks.
