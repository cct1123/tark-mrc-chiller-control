# Tark MRC150/300 controller

## Engineering objective

Build a small Python controller for the Tark Thermal Solutions MRC150/300 benchtop
recirculating chiller. One public `Chiller` API must support a simulator and a
physical device backend. Separate acquisition/control from the Plotly Dash GUI;
provide safe setpoint control, continuous temperature monitoring, live status and
history, CSV recording, and RS-232/RS-485 transport abstractions.

Source of intent: [session prompt 2](prompt%20log.md#prompt-2), extended by
[prompt 4](prompt%20log.md#prompt-4) and independently reviewed under
[prompt 5](prompt%20log.md#prompt-5). Complete and validate all hardware-independent
functionality, including ownership, recovery, reusable fault simulation,
append/resume CSV, type checks and sustained end-to-end operation. This does not
authorize physical commissioning or imply a validated hardware release.

## Requirements / acceptance criteria

All requirements are mandatory for the eventual system. Numeric software defaults
and test conditions marked **derived** are engineering choices, not device facts.
Current outcomes belong in [STATE.md](STATE.md); stable test methods and evidence
belong in [records/RECORDS.md](records/RECORDS.md).

| ID | Observable acceptance criterion | Validation |
| --- | --- | --- |
| REQ-001 | Preserve the template engineering loop, canonical state, evidence/decision ledger and report; link every requirement to a method and current outcome; record session prompts verbatim. | TEST-001 workspace inspection |
| REQ-002 | Both backends use `connect()`, `disconnect()`, `is_connected`, `get_temperature()`, `get_setpoint()`, `set_setpoint(value_c)`, `get_status()` through the same `Chiller` class; temperatures are Celsius. Disconnected reads/writes fail clearly. | TEST-002 common API tests; TEST-012 hardware |
| REQ-003 | Default distilled-water policy accepts finite real setpoints inclusively within 2–40 °C. Reject booleans, strings, NaN, infinities and out-of-range values before any backend write. | TEST-003 boundary and zero-write tests |
| REQ-004 | A different coolant requires an explicit named profile with finite ordered bounds and a documented source/rationale. No automatic sub-zero profile or range inferred from controller acceptance. | TEST-003 custom-profile tests and review |
| REQ-005 | No unverified Tark serial command or setting is supplied. Missing codec prevents port opening and transmission. Later protocol implementation is confined to `protocol.py` plus documented configuration and tests. | TEST-004 unavailable-protocol and codec-boundary tests |
| REQ-006 | RS-232 and RS-485 adapters share one transport contract; settings must be explicit. Software tests exercise bounded timeout, partial transfer, error cleanup and RS-485 configuration without any real port. Actual electrical/interface support remains a physical criterion. | TEST-005 injected serial fakes; TEST-012 hardware |
| REQ-007 | Serial transactions are serialized. Communication failures produce typed errors and explicit unavailable/stale state; no write retry or reconnect silently reapplies a setpoint, including a request queued in an older connection intent. Explicit disconnect is idempotent. | TEST-002, TEST-005, TEST-006, TEST-014 fault/lifecycle tests |
| REQ-008 | One active monitoring owner per Chiller and LiveState samples without a browser or Dash callback; duplicate workers/manual polling are refused. **Derived:** default 1 s interval, configurable positive finite interval; a 0.02 s test interval yields at least 3 samples within 2 s; stop joins all in-flight polling or reports a timeout. | TEST-006 monitor tests |
| REQ-009 | CSV records UTC time, elapsed seconds, measured temperature °C, setpoint °C, backend, connection and error information. Missing values stay blank, never zero-filled. Header once; flush each row; report file errors. | TEST-007 CSV round-trip and I/O-failure tests |
| REQ-010 | Shared snapshots are safe for concurrent reads; history is bounded (**derived:** 3,600 samples by default). Failed polls have no valid measurement; GUI marks errors and age instead of presenting old data as live. | TEST-006 and TEST-008 state/presentation tests |
| REQ-011 | Dash renders backend/connection/status and temperature plus setpoint versus time with explicit units; validated setpoint input uses the public API. Page refresh does not create another acquisition worker. | TEST-008 callbacks/HTTP smoke; TEST-011 browser review |
| REQ-012 | Simulator dynamics and API/error scenarios are repeatable with an injected clock or controlled fakes. End-to-end simulator → monitor → CSV/history → Dash works hardware-free; core imports do not require Dash. | TEST-002, TEST-009 integrated simulator run |
| REQ-013 | Neither API nor GUI claims coolant presence, leak, flow, level or alarm telemetry is available without protocol evidence; unknown is distinct from safe/normal. | TEST-008 UI/status review; TEST-012 capability validation |
| REQ-014 | Installation, exact tested dependency versions, run/test/shutdown instructions and limitations are reproducible. Initial source is importable and tests pass in an isolated environment. | TEST-010 install/package/static checks |
| REQ-015 | Before declaring the project validated, verify actual unit/controller identity, documented interface/settings, read/write semantics, setpoint readback and calibrated temperature behavior on physical hardware under reviewed conditions. | TEST-012 physical acceptance, currently external |
| REQ-016 | Simulator supports deterministic thermal response, configurable measurement update interval, optional seeded variation and reproducible connection/timeout faults. A reusable clearly synthetic protocol/serial fixture exercises SerialDevice without real ports or Tark syntax. | TEST-013 simulator/fake-stack tests |
| REQ-017 | Configured read recovery has finite reconnect budget and delay, recovers transient transport faults and reports exhaustion. Explicit disconnect cancels recovery intent; invalid writes and unknown/malformed protocol errors are never retried as writes. No write replay. | TEST-014 recovery/fault tests |
| REQ-018 | Explicit CSV append validates existing schema, avoids duplicate headers, preserves completed rows, and refuses malformed/truncated records unchanged. Concurrent logger owners are refused without changing the file. Session boundaries and elapsed-time resets remain distinguishable. | TEST-015 CSV resume tests |
| REQ-019 | A reproducible simulator → acquisition → CSV → Dash/state demonstration produces a cooling trajectory and survives browser refresh/absence. Sustained concurrent callbacks, injected faults, bounded history and clean shutdown pass; lint/type/build checks pass. | TEST-016 sustained end-to-end validation |

## Constraints

- No real-device discovery, serial port opening or physical actuation in this run.
- Validate setpoints at the public boundary and physical write boundary. Coolant
  policy is a software guard; it cannot establish what liquid is installed.
- Retain the template's candidate review phase before future hardware integration.
  No hardware candidate or integration approval exists yet.
- Do not invent commands, register maps, baud rates, framing, terminators,
  addresses, checksums, response syntax or serial safety telemetry.
- Use explicit coolant-dependent bounds. Controller menus are not equipment or
  coolant safety specifications; acceptance of a value is not evidence of safety.
- Do not issue speculative stop, reset, alarm configuration, or startup setpoint
  commands. Disconnecting software does not stop the physical chiller.
- Keep the implementation small: one Python package, a single acquisition worker,
  one bounded in-memory history, standard-library CSV, optional serial/GUI extras.

## Available system

- At initial inspection this repository had only `prompt log.md`, no commits,
  manuals, source, PROJECT.md, AGENTS.md, ARCHITECTURE.md or STATE.md.
- Framework: [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template),
  clean local checkout `724a7f772069d3357ea66dbc4742d25bd874a33e`; core operating
  instructions/architecture imported, project-specific state/report adapted.
- No attached manual is exposed in the current session or project files. Official
  online manual used provisionally; source and revision differences are recorded
  in [E002](records/RECORDS.md#e002). Do not represent it as the missing attachment.
- Controller communications manual, actual model suffix, controller model/firmware,
  interface variant, electrical wiring, serial settings and installed coolant are
  not available. See dependency EXT-001 in STATE.md.

## Assumptions / engineering choices

- Python 3.12 is the initial tested runtime; single local process, one device.
- GUI refresh and acquisition are independently scheduled; polling is not real
  time. Timing and simulator dynamics are software choices with no performance
  claim for the physical chiller.
- Simulator starts at 20 °C with a 20 °C target (test convenience, not a factory
  setting); its thermal model is uncalibrated and excludes fluid/flow physics.
- No unattended write retry or replay. Read reconnection is explicitly configured
  with a bounded outage budget; the default API remains conservative.
- This phase finishes only after all hardware-independent acceptance passes.
  Missing authoritative protocol and physical validation remain explicit external
  dependencies; software completion is not a hardware-ready or validated release.
