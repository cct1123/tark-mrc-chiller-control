# Prompt log

## Session — 2026-09-10

### Prompt 1

in this session follow the prompts AND keep the prompts in a "prompt log.md" file for record .

### Prompt 2

Use [https://github.com/cct1123/agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template) as the starting framework for this project.

Engineering objective:

Develop a simple, versatile Python controller for the Tark Thermal Solutions MRC150/300 benchtop recirculating chiller. The system must support RS-232 and RS-485 hardware abstraction, robust connection/error handling, safe temperature setpoint control, continuous temperature monitoring, CSV logging, and a Plotly Dash GUI showing live status and temperature versus time.

The same high-level API must work with both:

1. a simulated chiller, and
2. the real MRC150/300 hardware.

Acquisition/control must remain independent from the GUI. The architecture must allow the actual Tark/controller serial protocol to be inserted cleanly once authoritative communication documentation is available.

Take ownership of the project and operate autonomously. Read PROJECT.md, AGENTS.md, ARCHITECTURE.md, STATE.md, the attached MRC150/300 user manual, and all existing repository content before modifying anything.

First establish the engineering workspace and requirements.

Use the template's persistent engineering loop:
inspect → identify gap → design → implement → test → diagnose → update state → repeat.

You are the coordinator. Use bounded specialist/subagents when useful for manual analysis, architecture, safety, testing, GUI, serial communication, and code review, but integrate all conclusions into one coherent implementation and durable STATE.md.

Required actions:

- Adapt PROJECT.md to this specific controller project.
- Extract only facts actually supported by the MRC150/300 manual.
- Record assumptions separately from verified facts.
- Define observable requirement IDs and acceptance criteria.
- Establish a minimal software architecture before substantial implementation.
- Create only directories/files justified by the implementation.
- Preserve the template's STATE.md, records/RECORDS.md, and outputs/REPORT.md workflow.

Target architecture should separate at least:
```bash
application / public Chiller API
acquisition + monitoring service
device abstraction
protocol codec
serial transport
    RS232
    RS485
simulator
CSV/data logging
shared live state/history
Plotly Dash GUI
```

The simulator and physical controller must implement the same user-facing API.

Design a small API such as:

- connect()
- disconnect()
- is\_connected
- get\_temperature()
- get\_setpoint()
- set\_setpoint()
- get\_status()

Refine this only when there is a concrete engineering reason.

Safety requirements:

- Do not allow arbitrary temperature writes.
- Default to the manual-supported distilled-water envelope of 2–40 °C unless another coolant profile is explicitly configured.
- Support coolant-dependent bounds without hard-coding unsafe assumptions.
- Never infer that sub-zero operation is safe merely because the controller accepts the value.
- Validate every setpoint before any hardware command is issued.
- Do not imply that software can detect coolant presence, leaks, flow, or fluid level unless the actual communication interface proves those signals are available.

Critical protocol rule:

The supplied MRC150/300 user manual states that RS-232/RS-485 communication is available but delegates actual communications details to a separate controller-manufacturer manual.

Search the available project inputs for that communication manual.

If it is absent:

- DO NOT invent serial commands, register maps, baud rates, framing, terminators, addresses, checksums, or response syntax.
- Record this as an external protocol-information dependency.
- Continue all engineering that does not require those facts.
- Design the transport and protocol boundaries so the protocol can later be inserted in one localized module.
- Do not treat the missing protocol document as a reason to stop the overall project.

Define a hardware-free validation plan covering the entire software stack.

By the end of this run, leave:

- a clear PROJECT.md,
- current STATE.md,
- requirement/acceptance matrix,
- architecture decision record,
- minimal project/package scaffold,
- dependency configuration,
- initial tests,
- and a precise next engineering action.

Do not overengineer. Prefer the smallest architecture that cleanly supports simulator, real hardware, monitoring, logging, and Dash.

Continue autonomously until this initialization/design milestone is internally consistent and tested. Ask for human intervention only for a genuine external dependency that prevents further useful work.

### Prompt 3

review commit push


### Prompt 4

Continue the autonomous engineering project from the current repository state.

Read PROJECT.md, AGENTS.md, STATE.md, relevant records, tests, manual-derived requirements, and existing implementation. Reconcile unfinished or stale work before proceeding.

Now build and validate the complete hardware-independent system.

Do not wait for physical MRC150/300 hardware and do not wait for the missing controller communication protocol. Everything that can be engineered and tested without those dependencies should be completed now.

Use the persistent loop:
inspect → highest-value gap → implement → test → diagnose → refine → update STATE.md → repeat.

Use specialist/subagents for bounded work when useful, while maintaining one coordinator responsible for integration.

Implement a compact production-quality Python package containing:

1. Common chiller API
Create one stable high-level interface used identically by simulator and physical-device implementations.

2. Simulator
Implement a useful simulated MRC chiller capable of end-to-end development.

It should model enough behavior to exercise the application:
- connection/disconnection
- current temperature
- temperature setpoint
- gradual thermal response toward the setpoint
- optional realistic small measurement variation
- configurable update rate
- simulated communication failures/timeouts where useful
- deterministic mode for tests

Avoid unnecessary physical modeling.

3. Serial transport abstraction
Implement reusable pyserial-based transport infrastructure for RS-232 and RS-485.

Transport responsibilities may include:
- port configuration
- open/close
- read/write primitives
- timeout handling
- locking where required
- clear exception translation
- reconnection support
- RS-485 configuration

Do NOT place Tark command syntax in this layer.

4. Protocol abstraction
Create a narrow codec/protocol interface between transport bytes and device-level operations.

Because the authoritative communication protocol is currently unavailable, do not fabricate Tark commands.

Provide a test/fake protocol implementation so the physical-device class and error-handling paths can be exercised without hardware.

5. Robust device behavior
Implement:
- idempotent connect/disconnect
- clear connection state
- timeout handling
- controlled retry/reconnect behavior
- meaningful typed exceptions
- no silent failure
- safe recovery after transient errors
- clean shutdown

Avoid infinite retry loops.

6. Safe setpoint control
Centralize validation.

Default coolant profile:
distilled water, 2–40 °C.

Allow explicitly configured alternative coolant profiles, but require known minimum/maximum bounds before allowing values outside the default water range.

Reject NaN, infinity, malformed values, and out-of-range values before reaching the protocol layer.

7. Continuous acquisition service
Temperature acquisition must operate independently of Dash.

Create a background monitoring component with:
- configurable sample interval
- start/stop lifecycle
- timestamped measurements
- current state
- bounded in-memory history
- error/status reporting
- clean thread/task shutdown
- no dependency on Dash

The chiller must continue monitoring even when no browser is connected.

8. CSV logging
Implement reliable appendable logging with useful fields such as:
timestamp,
temperature,
setpoint,
connection/status,
and error information where appropriate.

Ensure headers, path handling, flushing/closing, and interrupted sessions behave sensibly.

9. Plotly Dash GUI
Build a simple laboratory-control GUI showing:
- connection status
- current temperature
- current setpoint
- setpoint entry
- safe Apply action
- clear validation/error feedback
- live temperature-versus-time graph
- logging/monitoring status where useful

The GUI must consume the acquisition service/state; it must not own the serial acquisition loop.

A browser refresh or closing the browser must not stop device monitoring.

10. Tests
Build comprehensive hardware-free tests.

At minimum test:
- simulator API
- setpoint validation
- coolant profiles
- acquisition lifecycle
- CSV output
- connection failures
- timeouts
- reconnect behavior
- malformed protocol responses through a fake protocol
- concurrent GUI/acquisition access where relevant
- Dash callbacks at an appropriate unit/integration level
- clean shutdown

Provide one end-to-end demonstration using the simulator:
simulated chiller → acquisition → logging → Dash/state → temperature trajectory.

Run formatting/linting/type checks/tests/build checks appropriate to the project.

Fix failures rather than merely recording them.

Continuously update STATE.md and durable evidence.

Do not polish documentation extensively yet; prioritize a complete, working, hardware-free engineering system.

Stop this phase only when all requirements that do not depend on the proprietary/unknown serial protocol or physical hardware are demonstrably PASS, with remaining external dependencies explicitly isolated.

## Prompt 5

Perform a fresh engineering review of the entire Tark MRC150/300 controller project.

Do not assume the existing implementation is correct because tests currently pass.

Read PROJECT.md, STATE.md, records, architecture, implementation, tests, and the supplied manuals. Inspect the repository as a new senior engineer would.

Use independent specialist/subagents where useful for:

- Python/API architecture
- serial communications
- concurrency
- safety
- fault injection/testing
- Plotly Dash
- code simplification

The coordinator must reconcile their findings and implement the corrections.

Run repeated review → test → diagnose → repair loops until no consequential software gap remains.

Focus especially on:

ARCHITECTURE
Verify that GUI, acquisition, device logic, protocol, and serial transport are genuinely decoupled.

The desired dependency direction is approximately:

Dash/UI
↓
application/service
↓
common Chiller API
↓
device implementation
↓
protocol
↓
serial transport

Simulator should substitute at the Chiller/device boundary without special behavior elsewhere.

Remove unnecessary abstraction layers, factories, managers, adapters, configuration machinery, agent-generated scaffolding, duplicated utilities, premature plugin systems, and speculative functionality.

Prefer clear Python over framework-heavy architecture.

SERIAL ROBUSTNESS
Exercise:

- missing COM/tty port
- permission failure
- port already open
- read timeout
- write failure
- mid-run cable/device disconnect
- truncated response
- malformed response
- unexpected response
- delayed response
- repeated reconnect attempts
- shutdown while a read is pending

Verify failures propagate into understandable application state rather than killing acquisition silently.

CONCURRENCY
Check for:

- serial access races
- GUI/acquisition races
- deadlocks
- stale state
- unbounded threads
- duplicate monitoring loops
- duplicate CSV writers
- inability to terminate cleanly

Make ownership of the device connection explicit.

SAFETY
Attempt to defeat setpoint validation through:

- GUI callbacks
- direct API calls
- configuration
- NaN/Inf
- strings
- unit mistakes
- reconnect/replay behavior

There must be one authoritative validation path before any physical write.

Do not automatically resend a stale setpoint after reconnect unless this behavior is explicitly designed, safe, and documented.

PROTOCOL READINESS
Search all available inputs again for an authoritative controller communication manual.

If it is available:

- extract the protocol exactly from the source,
- cite/document each implemented command,
- implement it only inside the protocol layer,
- create byte-level golden/fixture tests,
- preserve raw examples as test fixtures where appropriate,
- do not infer undocumented behavior.

If it is still unavailable:

- do not invent it,
- ensure the missing information is isolated to a small protocol implementation,
- make the exact required information explicit:
  baud/parity/data bits/stop bits,
  RS-485 addressing if applicable,
  command syntax/registers,
  terminators/framing,
  read-temperature command,
  read-setpoint command,
  write-setpoint command,
  acknowledgements/errors,
  checksum/CRC if any.
- validate the rest of the hardware stack with fake serial/protocol fixtures.

TEST QUALITY
Look for tests that merely mirror the implementation.

Add fault-injection and integration tests that would fail under realistic regressions.

Verify that the simulator-to-Dash path and fake-serial-to-device path both exercise real production code.

Then perform a major cleanup pass.

Prune:

- obsolete code
- abandoned prototypes
- unnecessary dependencies
- duplicate documentation
- unused configuration
- dead tests
- excessive comments
- generated scaffolding that no longer serves the project

Keep the repository small and understandable.

Run the complete quality suite from a clean environment if practical.

Update STATE.md and records with actual evidence rather than activity logs.

At completion of this phase, all hardware-independent requirements should be PASS. Any remaining BLOCKED requirements must be genuinely dependent on either:

1. the missing authoritative communication protocol, or
2. access/authorization for a physical MRC150/300.

Do not stop for a problem that can still be solved locally.

## Prompt 6

review prune commit push
