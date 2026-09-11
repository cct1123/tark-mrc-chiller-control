# Controller software architecture

The package uses synchronous Python and one background thread for monitoring. The
engineering loop and future hardware review gate remain in [AGENTS.md](../AGENTS.md).
Source framework: [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template),
commit 724a7f772069d3357ea66dbc4742d25bd874a33e. Project decisions: D001–D004 in
[records](../records/RECORDS.md); observable criteria: [PROJECT.md](../PROJECT.md).

## Dependencies and ownership

The CLI creates Chiller, Monitor, CsvLogger and Dash. Monitor supplies LiveState
when the caller does not provide one. The
[system overview](../docs/assets/system.svg), [device paths](../docs/assets/modes.svg)
and [data flow](../docs/assets/data-flow.svg) show the boundaries. Lab instructions
and runnable Python examples are in the [user guide](../docs/usage.md).

The device uses the protocol codec to encode a command, asks the transport to
exchange bytes, then decodes the reply. The codec and transport do not import
each other. The codec supplies a function that tells the transport when a reply
is complete. GUI, API and monitor contain no serial commands.

The application shares one Chiller instance between controls and monitoring for
each device. Only one Monitor may use a Chiller and LiveState at a time; competing
workers or manual polls are rejected. A stopped monitor releases them only after
all its polling has finished. A file lock allows only one CsvLogger to write to a
CSV. Samples and counters are published together under a lock; readers receive
snapshots that cannot be changed.

## Boundaries

| Module | Responsibility |
| --- | --- |
| api.py / device.py | Common seven-operation API, one operation at a time, limited read-recovery attempts |
| safety.py | One setpoint validator, used by the API and device before writes |
| simulator.py | Device interface with a repeatable temperature model and optional noise, timing and faults |
| hardware.py | Device operations through the codec and transport; checks decoded results |
| protocol.py | Device command/reply format; MissingProtocol blocks connection before opening a port |
| transport.py | Explicit pySerial settings, time/size limits, locking, cleanup and RS232/RS485 modes |
| monitoring.py | Background or manual polling, samples, limited history, errors and counters |
| csvlog.py | CSV checks, append sessions, one writer per file; stops recording after a write error |
| gui.py | Displays snapshots and calls the API when the user submits a control |
| __main__.py | Creates and stops the simulator application |
| development/testing.py | Software serial stand-in and test protocol that exercise the production code |

The core API and monitoring use only the Python standard library. Dash/Plotly and
pySerial are optional packages. A replaceable serial constructor allows tests to
use software endpoints and lets the core load without pySerial installed.

## Safety and lifecycle

All temperatures are Celsius. One CoolantProfile.validate implementation enforces
finite numeric values and explicit bounds before writes. Default distilled-water
bounds are 2–40 °C; alternatives require provenance at API and backend. Configured
policy does not establish installed coolant, leaks, flow, fluid level or alarms.

A reconnect never sends a target. Failed/uncertain writes are never replayed.
A waiting setpoint request is cancelled if a newer connect/disconnect action
occurs before the write begins. Read recovery has a fixed attempt limit for each
outage; an invalid protocol reply stops automatic recovery.

Shutdown order: request monitor stop, disconnect the serialized Chiller (cancels
recovery), wait for the monitor to finish, close CSV. A cancelled poll may publish one
unavailable final row. A timeout reports that polling is still active; its logger
must remain open. Uncooperative OS/backend calls cannot be forcibly interrupted.

Readings are sequential with conservative poll-start timestamps, not atomic
hardware snapshots. GUI connection wording describes the last poll and its age.
Serial deadlines use a high-resolution monotonic clock; scheduling is not hard
real time. Never start multiple processes to share a physical device.

Interrupted worker startup cannot release an active polling owner. Serial I/O
interruptions close the endpoint, and an interrupted CSV write prevents reuse
of that writer. Worker termination leaves a visible service error. The launcher
handles cooperative termination through the same shutdown sequence; a forced
OS kill cannot run Python cleanup. See the regressions and rationale in
[E027 / D005](../records/RECORDS.md#e027).

## Protocol readiness

EXT-001 remains external. Obtain the matching controller model/firmware and manual
before implementing baud/parity/data bits/stop bits, RS485 addressing, command or
register syntax, framing/terminators, temperature/setpoint reads, setpoint write,
acknowledgements/errors, checksum/CRC, units/scaling, response correlation, timing,
pinout and startup/read side effects. Document each implemented command and add
source-derived byte fixtures. No protocol or physical behavior is guessed.

The synthetic fixture's JSON envelopes/settings are test data only. Real-unit
validation and exact first interactions require the authoritative codec, confirmed
hardware/coolant/setup and the review gate in AGENTS.md.

## Validation reproduction

[Development guide](README.md#run-the-checks) has the current install, quality and
sustained-run commands. The ten-minute baseline is recorded in
[E019](../records/RECORDS.md#e019); later records state which code each run tested.
Bootstrap CSS and its license ship in package assets. No CDN is needed at runtime.
Researcher examples use the package-root Chiller, Monitor and CsvLogger classes;
synthetic serial fixtures remain in development and are not in the installed wheel.
