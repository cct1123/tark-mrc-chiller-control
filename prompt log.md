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
