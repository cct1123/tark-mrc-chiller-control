# Controller software architecture

The package uses ordinary synchronous Python and one acquisition thread. The
engineering loop and future hardware review gate remain in [AGENTS.md](AGENTS.md).
Source framework: [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template),
commit 724a7f772069d3357ea66dbc4742d25bd874a33e. Project decisions: D001–D003 in
[records](records/RECORDS.md); observable criteria: [PROJECT.md](PROJECT.md).

## Dependencies and ownership

    CLI application ──creates/owns──> Chiller, Monitor, LiveState, CsvLogger, Dash
    Dash ──refresh──> LiveState (no device reads or worker lifecycle)
    Dash ──explicit control──> Chiller
    Monitor ──poll──> Chiller
    Monitor ──publish──> LiveState and SampleSink (implemented by CsvLogger)
    Chiller ──serialized operations──> ChillerDevice
                                      ├─ SimulatedDevice
                                      └─ SerialDevice ──> ProtocolCodec
                                                     └─> RS232/RS485 transport

The device orchestrates encoding, transport exchange and decoding; codec and
transport do not import each other. The transport accepts a response-completion
predicate from the codec. GUI, API and monitor contain no serial commands.
There is no manager, plugin framework, database, command queue or GUI-owned loop.

The application creates exactly one Chiller facade for each device and shares it
between control and acquisition. One Monitor may actively own a Chiller and
LiveState; another worker or competing manual poll is rejected. A stopped monitor
releases ownership only after its polling is quiescent. A CSV file has one logger
owner, enforced on its open descriptor. Immutable snapshots and their counters
publish together under the state lock.

## Boundaries

| Module | Responsibility |
| --- | --- |
| api.py / device.py | Seven-operation public API, structural device contract, serialized access and finite read recovery |
| safety.py | Single authoritative Celsius/profile validator, reused by API and device before writes |
| simulator.py | Same device contract; deterministic thermal response, optional noise/cadence/faults |
| hardware.py | Maps device operations through codec/transport; validates normalized results |
| protocol.py | Sole insertion point for documented wire semantics; MissingProtocol fails before opening |
| transport.py | Explicit pySerial configuration, bounded exchange, locking, cleanup, RS232/RS485 modes |
| monitoring.py | Worker/manual-poll lifecycle, immutable samples, bounded history, errors and recording counters |
| csvlog.py | Validated append/session schema, file ownership, flush/close and failed-write latch |
| gui.py | Snapshot presentation and explicit API callbacks only |
| __main__.py | Simulator application composition and shutdown |
| testing.py | Clearly synthetic protocol and memory endpoint through real production layers |

Core API/acquisition need only the standard library. Dash/Plotly and pySerial are
optional runtime extras. Serial factories exist only for lazy optional imports
and hardware-free injection, not speculative backend selection.

## Safety and lifecycle

All temperatures are Celsius. One CoolantProfile.validate implementation enforces
finite numeric values and explicit bounds before writes. Default distilled-water
bounds are 2–40 °C; alternatives require provenance at API and backend. Configured
policy does not establish installed coolant, leaks, flow, fluid level or alarms.

A reconnect never sends a target. Failed/uncertain writes are never replayed.
A queued control retains its original connection intent and is cancelled if an
explicit connect/disconnect supersedes that intent before the write begins.
Read recovery has a finite per-outage budget; malformed protocol suspends it.

Shutdown order: request monitor stop, disconnect the serialized Chiller (cancels
recovery), join the monitor, close CSV. An in-flight cancelled poll may publish one
unavailable final row. A timeout reports that polling is still active; its logger
must remain open. Uncooperative OS/backend calls cannot be forcibly interrupted.

Readings are sequential with conservative poll-start timestamps, not atomic
hardware snapshots. GUI connection wording describes the last poll and its age.
Serial deadlines use a high-resolution monotonic clock; scheduling is not hard
real time. Never start multiple processes to share a physical device.

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
