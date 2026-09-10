# Fresh engineering review — MRC150/300 controller

2026-09-10. The independent review found and repaired consequential software
defects that the earlier passing suite missed. **349 tests now pass in a fresh
environment, and the final ten-minute fault run passed.** All hardware-independent
requirements are PASS. Real communication
remains unavailable pending the authoritative controller protocol; no hardware
was accessed and this is not a physically validated release.

## Findings and corrections

| Finding | Resulting behavior |
| --- | --- |
| Queued setpoint crossed disconnect/reconnect | A write retains its original connection intent; a superseding lifecycle action cancels it before device access. No write replay. |
| Competing monitors/manual polls and incomplete stop | One active owner per Chiller/state; overlap is rejected and stop joins active polling or reports a timeout while retaining ownership. |
| Competing CSV append writers | Descriptor-based OS locks exclude another logger, including another process or file alias, while allowing readers. |
| CSV failure killed acquisition or left misleading counters | Recording failures disable the sink; acquisition continues. Sample/count publication is atomic. A successful replacement recording clears the old fault. |
| False Windows serial timeouts | High-resolution monotonic deadlines replace the coarse clock. A 5,000-transaction regression covers immediate replies with short budgets. |
| Specific port failures hidden in GUI | Missing/denied/busy reasons survive through the device, monitor and displayed error. Connection text explicitly describes the last poll and its age. |

Three specialists reviewed serial behavior, safety/Dash, and concurrency/CSV.
The coordinator reconciled changes and requested a separate cross-review of the
ownership corrections. Reproductions and methods: [E017–E018](../records/RECORDS.md#e017).

## Architecture and cleanup

The implementation remains one small standard-library core with optional Dash /
Plotly and pySerial extras. Simulator and serial device substitute at the same
device boundary. GUI refresh reads immutable state; explicit GUI controls and
the independent monitor use the same public API. The device orchestrates codec
and transport; actual wire syntax belongs only in protocol.py.

[ARCHITECTURE.md](../ARCHITECTURE.md) now documents actual dependencies and ownership;
AGENTS.md retains the template's engineering loop. Removed generic duplicate
architecture/ledger scaffolding, redundant runtime protocol-presence checks and
unused scratch demonstrations. Behavioral tests, small injection interfaces and
historical evidence remain. No new runtime dependency or framework was added.
Requirements live in [PROJECT](../PROJECT.md), current outcomes in
[STATE](../STATE.md), usage in [README](../README.md), historical facts/decisions
in [records](../records/RECORDS.md), and user requests in [prompt log](../prompt%20log.md).

## Validation

| Check | Evidence |
| --- | --- |
| Fresh Python 3.12.14 environment, exact 42-package snapshot | requirements-tested.txt; E018 |
| Complete fault/concurrency/integration suite | 349 PASS, zero failures/errors, 9.82 s; [JUnit](review-tests.xml) |
| Ruff lint / format / mypy / dependency consistency | PASS; 24 Python files / 14 package modules; E018 |
| Source archive → wheel and wheel-only execution | PASS; all source bytes match, typing marker/demo included; [build log](review-build.txt) |
| Simulator and both fake serial adapters without optional imports | PASS under python -S; E018 |
| Final-source 600-second concurrent fault run | PASS; 596 sample/CSV rows, 120 history, 4,007 refreshes, 401 invalid writes rejected; [summary](review-soak.json) |
| Browser status / safe control / disconnect / reconnect / reload | PASS; 173 rows continued after tab closure; [E019](../records/RECORDS.md#e019) |

The test stack exercises production transport, device, API, acquisition, CSV,
state and actual Dash HTTP callbacks. Fixtures are explicitly synthetic; their
settings and JSON protocol do not describe Tark hardware. Serial cases include
open failures, partial transfers, missing/delayed/truncated/malformed/stale replies,
write failure/lost acknowledgement, disconnect, finite reconnect and pending-read
shutdown. Invalid values are rejected through direct API, backend and GUI paths.

The sustained run applied exactly three targets and ended at simulated 17.998355 °C
for an 18 °C target. Its seven opens match the planned faults/reconnects exactly;
the old unexplained deadlines did not recur. Workers, files and preview closed.
The generated CSV and Plotly HTML stay local; reproduce them using the
[README demonstration](../README.md#hardware-free-end-to-end-demonstration).
[Final source/config hashes](source-manifest.sha256) identify the tested code.
E019 supersedes the earlier sustained result; no source change overlapped this run.

## Remaining external dependencies

The repository and exposed attachments still contain no applicable controller
communication manual. Official Tark Rev 13 was re-read and its relevant pages
visually checked; it delegates communication details to that separate document.
[E002](../records/RECORDS.md#e002) records source/hash and verified manual facts;
[E017](../records/RECORDS.md#e017) records the renewed search and a current product
page's differing coolant guidance that must be resolved for the actual unit.

Next: obtain the matching controller manual and model/firmware identity. Extract
baud, parity, data/stop bits, RS485 addressing, command/register syntax, framing /
terminators, temperature and setpoint reads, setpoint write, acknowledgements /
errors, checksum/CRC, units, timing and response identity. Implement only the
documented codec with source-derived byte fixtures and explicit configuration.
MissingProtocol continues to prevent port opening until then.

After protocol acceptance, prepare the exact physical read-first procedure,
reviewed coolant limits, expected responses, shutdown/rollback and calibrated
checks for the template's hardware review gate. Physical REQ-002/006/015 remain
blocked by documentation and actual-unit access/setup/authorization.

Software cannot establish installed coolant, flow, leaks, level or physical
safety. Reads are sequential, scheduling is not hard real time, CSV flush does
not guarantee power-loss durability, and backend calls ignoring timeouts cannot
be forcibly interrupted. Windows was exercised; POSIX locking and actual RS485
adapter behavior are not claimed as physically validated.
