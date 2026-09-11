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
functionality, including explicit ownership, bounded recovery, fault testing,
type checks and sustained end-to-end operation. This does not
authorize physical commissioning or imply a validated hardware release.
[Prompt 7](prompt%20log.md#prompt-7) adds lab-user documentation, verified examples
and visuals; physical validation remains out of scope.
[Prompt 8](prompt%20log.md#prompt-8) requests a new-user language review, cleanup,
commit and push of the documentation.
[Prompt 9](prompt%20log.md#prompt-9) adds a simpler researcher workflow, a Bootstrap
dashboard, numbered examples and illustrated setup/API/hardware guides. Protocol
and physical acceptance must remain separate from simulator success.
[Prompt 10](prompt%20log.md#prompt-10) requests a reproducible software release
candidate, a fresh requirements audit, normal termination checks and a precise
staged resumption procedure. Physical work requires the matching protocol,
identified equipment, known settings and safe setup; writes need explicit approval.

[Prompt 12](prompt%20log.md#prompt-12) changes the design to a compact reusable
device driver. Version 0.2 removes the old internal APIs without compatibility
modules. Six functional modules plus package/module entry files replace the
fragmented implementation. Monitoring/CSV and Dash remain optional clients.
CSV append/resume, simulator noise/fault configuration, global ownership
registries and GUI connection management are removed by this simplification.
Updated criteria below supersede the corresponding 0.1 criteria; earlier results
remain historical evidence only. Decision D006 records the tradeoff.
[Prompt 13](prompt%20log.md#prompt-13) requests a holistic review, corrections,
updated evidence, commit and push without changing the compact-driver scope.

[Prompt 14](prompt%20log.md#prompt-14) makes the researcher examples hardware-only
and requests readable, idiomatic Python. One editable lab connection file supplies
explicit serial settings and a documented codec; it opens nothing on import.
No example falls back to a simulator or selects an automatic hardware target.
The simulator remains a development/test backend. This changes the user workflow
in REQ-020/021/022; it does not supply the missing protocol or authorize device use.

## Requirements / acceptance criteria

All requirements are mandatory for the eventual system. Numeric software defaults
and test conditions marked **derived** are engineering choices, not device facts.
Current outcomes belong in [STATE.md](STATE.md); stable test methods and evidence
belong in [records/RECORDS.md](records/RECORDS.md).

| ID | Observable acceptance criterion | Validation |
| --- | --- | --- |
| REQ-001 | Preserve the template engineering loop, canonical state, evidence/decision ledger and report; link every requirement to a method and current outcome; record session prompts verbatim. | TEST-001 workspace inspection |
| REQ-002 | Both backends use `connect()`, `disconnect()`, `is_connected`, `read_temperature()`, `read_setpoint()`, `set_setpoint(value_c)`, `read_status()` through the same `Chiller` class; temperatures are Celsius. Disconnected reads/writes fail clearly. | TEST-002 common API tests; TEST-012 hardware |
| REQ-003 | Default distilled-water policy accepts finite real setpoints inclusively within 2–40 °C. Reject booleans, strings, NaN, infinities and out-of-range values before any backend write. | TEST-003 boundary and zero-write tests |
| REQ-004 | A different coolant requires an explicit named profile with finite ordered bounds and a documented source/rationale. No automatic sub-zero profile or range inferred from controller acceptance. | TEST-003 custom-profile tests and review |
| REQ-005 | No unverified Tark serial command or setting is supplied. Missing codec prevents port opening and transmission. Later protocol implementation is confined to `serial.py` plus documented configuration and tests. | TEST-004 unavailable-protocol and codec-boundary tests |
| REQ-006 | One serial backend supports RS-232 and explicit native RS-485 configuration; settings must be explicit. Software tests exercise bounded timeout, partial transfer, error cleanup and RS-485 configuration without any real port. Actual electrical/interface support remains a physical criterion. | TEST-005 injected serial fakes; TEST-012 hardware |
| REQ-007 | Serial transactions are serialized. Communication failures produce typed errors and explicit unavailable/stale state; no write retry or reconnect silently reapplies a setpoint, including a request queued in an older connection intent. Explicit disconnect is idempotent. | TEST-002, TEST-005, TEST-006, TEST-014 fault/lifecycle tests |
| REQ-008 | Chiller explicitly starts at most one monitor, independent of a browser. Duplicate starts are refused; stop joins it while leaving synchronous access usable. **Derived:** default 1 s interval, configurable positive finite interval; a 0.02 s test interval yields at least 3 samples within 2 s; stop joins all in-flight polling or reports a timeout. | TEST-006 monitor tests |
| REQ-009 | CSV records UTC time, elapsed seconds, measured temperature °C, setpoint °C, backend, connection and error information. Missing values stay blank, never zero-filled. Header once; flush each row; report file errors. | TEST-007 CSV round-trip and I/O-failure tests |
| REQ-010 | Shared snapshots are safe for concurrent reads; history is bounded (**derived:** 3,600 samples by default). Failed polls have no valid measurement; GUI marks errors and age instead of presenting old data as live. | TEST-006 and TEST-008 state/presentation tests |
| REQ-011 | Dash renders backend/connection/status and temperature plus setpoint versus time with explicit units; validated setpoint input uses the public API. Page refresh does not create another acquisition worker. | TEST-008 callbacks/HTTP smoke; TEST-011 browser review |
| REQ-012 | Simulator dynamics and API/error scenarios are repeatable with an injected clock or controlled fakes. End-to-end simulator → monitor → CSV/history → Dash works hardware-free; core imports do not require Dash. | TEST-002, TEST-009 integrated simulator run |
| REQ-013 | Neither API nor GUI claims coolant presence, leak, flow, level or alarm telemetry is available without protocol evidence; unknown is distinct from safe/normal. | TEST-008 UI/status review; TEST-012 capability validation |
| REQ-014 | Installation, exact tested dependency versions, run/test/shutdown instructions and limitations are reproducible. Initial source is importable and tests pass in an isolated environment. | TEST-010 install/package/static checks |
| REQ-015 | Before declaring the project validated, verify actual unit/controller identity, documented interface/settings, read/write semantics, setpoint readback and calibrated temperature behavior on physical hardware under reviewed conditions. | TEST-012 physical acceptance, currently external |
| REQ-016 | Simulator provides deterministic thermal response with an injected clock. Clearly synthetic protocol/serial test fixtures exercise production SerialDevice, Chiller and monitoring through connection/timeout faults without real ports or Tark syntax. | TEST-013 simulator/fake-stack tests |
| REQ-017 | Configured read recovery has finite reconnect budget and delay, recovers transient transport faults and reports exhaustion. Explicit disconnect cancels recovery intent; invalid writes and unknown/malformed protocol errors are never retried as writes. No write replay. | TEST-014 recovery/fault tests |
| REQ-018 | Each monitoring run creates a new CSV exclusively. Existing files are refused unchanged, including files owned by another controller. No append/resume or independent logger lifecycle is exposed. | TEST-015 new-file and writer-exclusion tests |
| REQ-019 | A reproducible simulator → acquisition → CSV → Dash/state demonstration produces a cooling trajectory and survives browser refresh/absence. Sustained concurrent callbacks, injected faults, bounded history and clean shutdown pass; lint/type/build checks pass. | TEST-016 sustained end-to-end validation |
| REQ-020 | Concise guides teach installation, explicit hardware configuration, reads, operator-selected setpoint changes, CSV and Dash. Hardware examples use the production serial path with no simulator fallback and clearly refuse incomplete configuration. Their software behavior is exercised with fake serial I/O; physical execution stays blocked pending protocol and equipment. Screenshots and validation scopes are labeled accurately; local links and diagrams render. | TEST-017 documentation examples, links and visual review |
| REQ-021 | A responsive dash-bootstrap-components dashboard shows separate temperature/setpoint, connection/fault/recording status and live history. Styling works offline after installation. Five numbered examples run through the common API and optional monitoring; API reference and illustrated hardware tutorial identify every unsupported step. Developer test fixtures and records stay outside the main researcher path. Simplification preserves safety/recovery coverage. | TEST-018 example subprocesses, packaged assets, browser layout, source review and full regression |
| REQ-022 | Normal non-editable source/wheel installation passes in a fresh environment. Hardware examples provide read-only recording, continuous monitoring and a direct Dash client with Ctrl-C cleanup. Module/console launchers remain explicitly labeled simulator development tools. Interrupted startup/transactions/writes preserve ownership or refuse uncertain reuse. One lab configuration file uses real types, no guessed settings and blocks incomplete setup. Guides give exact operation and staged hardware resumption. | TEST-019 release audit, signals, package/install and template tests |

| REQ-023 | The package has roughly 5–7 functional modules, plus entry files; no compatibility shims, global registries or implicit workers. A few lines of synchronous Python create/use/close a controller with no GUI, serial package or service dependency. Monitoring/CSV are optional and connection ownership is explicit. | TEST-020 module inventory, import isolation, multi-controller and public API tests |

## Constraints

- No real-device discovery, serial port opening or physical actuation while the
  protocol, identified equipment, known settings and safe setup are unavailable.
- One authoritative validator in Chiller guards every public setpoint request
  before any backend I/O. Raw backend hooks are private; applications use Chiller.
  Coolant bounds cannot establish what liquid is installed.
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
