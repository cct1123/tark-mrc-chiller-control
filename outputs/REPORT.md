# Engineering report — laboratory driver 0.2.2

## Objective and outcome

Provide a small Python controller that researchers can read and integrate into
an existing experiment. The five numbered examples now use the physical serial
backend through one explicit lab connection file. There is no simulator fallback,
automatic target selection or global device instance. Simulator utilities remain
in development documentation and tests.

**209 tests pass in a fresh, non-editable Python 3.12.14 installation.** Real MRC
operation remains unavailable because the matching controller protocol is missing.
No physical port was opened and no hardware validation is claimed.

## Architecture and readability

![Architecture](../docs/assets/system.svg)

Six functional modules plus two entry files contain 1,311 Python lines:
controller for the synchronous API and safety; serial for configuration, codec
and I/O; simulator for software development; monitor for polling, CSV and history;
gui for the optional Dash client; errors for protocol exceptions.

Names identify retry budgets and sample counts. Keyword dataclass construction
replaces positional booleans/counters; control flow avoids nested conditionals
inside expressions. Python 3.12 type parameters replace the global TypeVar.
Required locks, cancellation and bounded recovery remain. No new package module,
framework, compatibility layer or dependency was introduced.

The public API is connect/disconnect, is_connected, read_temperature,
read_setpoint, set_setpoint, read_status, start_monitoring and stop_monitoring.
Import and construction perform no I/O. Monitoring is optional; its interval is
fixed for each run. Dash uses the same controller as ordinary experiment code.
[API guide](../docs/api.md), [architecture](../development/architecture.md).

## Evidence and corrections

[STATE](../STATE.md) maps every requirement. Hardware-independent acceptance
passes; physical portions of REQ-002/006/020 and REQ-015 remain blocked by the
missing protocol and identified/authorized setup. [E033/E034](../records/RECORDS.md#e033)
record the review and current validation; [D007](../records/RECORDS.md#d007)
records the revised user-example workflow.

| Check | Evidence |
| --- | --- |
| Installed unit/integration/fault/examples | 209 PASS; [JUnit](human-installed-tests.xml) |
| Source archive | Complete suite and packaged example/configuration files; [JUnit](human-archive-tests.xml) |
| Static checks | Ruff lint/format, mypy eight modules, pip check PASS |
| Build/install | Source archive and wheel, pinned environment, source/installed integrity; [build](human-build.txt), [audit](human-review.json) |
| Sustained integration | 60 s, 1,763 CSV rows, 1,527 concurrent Dash callbacks, history capped at 25, clean shutdown; [run](human-soak.txt) |
| Hardware examples | Production SerialDevice with test-only byte protocol and memory endpoints; no Simulator in the example path |
| User documentation | README/API blocks exercised with the configured serial path; links, SVG syntax and updated diagram checked |

A mutable monitoring interval could bypass its validation and produce rapid
polling: NaN reassignment yielded 2,306 samples in 30 ms during diagnosis. The
interval is now read-only. An event-controlled regression proves rejected
reassignment, a positive scheduled wait, one sample and clean shutdown.

During example review, a successful write acknowledgement followed by a different
readback still printed confirmation. The example now reports an unconfirmed
target, with no assumed rounding tolerance or retry. Each configured controller
also receives its own codec instance so separate instruments cannot share mutable
parser state. Native RS485 direction settings are documented separately from the
physical adapter/interface.

The GUI screenshot is an actual earlier simulator session, retained as a labeled
interface illustration. Layout/styles are unchanged; equivalent presentation
logic and callbacks were revalidated. A Plotly dependency emitted one existing
scattermapbox deprecation warning during the full suite; no map trace is used.

## Operation

From the project root in Windows PowerShell, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[serial]"
.\.venv\Scripts\python examples/01_read_temperature.py
```

The shipped configuration deliberately reports **Hardware not configured** with
no port opening. Once the documented codec, configuration and physical candidate
are approved, this same command reads the actual temperature, setpoint and status.

Edit examples/connection.py once with the verified SerialSettings and CODEC_CLASS;
the helper constructs a fresh codec and disconnected Chiller for each call.
RS485_MODE supplies native direction settings only if required by the verified
adapter. These are ordinary example settings, not an installed configuration system.
Existing applications can construct Chiller and SerialDevice directly.

After read validation, example 03 records five seconds; example 04 records until
Ctrl+C. Each has an editable CSV path and refuses existing files. Example 02 takes
one required operator-selected Celsius target; use only after authorization for
that change. Example 05 needs the GUI extra:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui,serial]"
.\.venv\Scripts\python examples/05_launch_dashboard.py
```

It serves http://127.0.0.1:8050 and records to outputs/05_dashboard.csv.
Ctrl+C closes monitoring, CSV and the connection; closing the browser does not.
Disconnecting software does not power off the chiller. A stop timeout means
cleanup remains pending. [Quick start](../docs/quickstart.md),
[examples](../README.md#numbered-hardware-examples),
[development-only simulator](../development/README.md#simulator-utilities).

## Protocol, limits and resumption

The official [MRC150/300 User Manual Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf)
uses distilled-water limits 2–40 °C in its p7 table. Page 12 mentions RS232/RS485
but delegates protocol details to a separate controller manual. Its interface
variant changes must be checked against the actual unit. The original attachment
was unavailable; the official cached PDF still matches E002's source hash.

No Tark codec, command, register, baud rate, pinout or telemetry was invented.
Software cannot establish coolant presence, leaks, flow, level or physical safety.
Custom limits require a coolant name and documented source; accepted controller
values do not prove sub-zero safety. Writes are never retried or replayed.
Read recovery is disabled by default and bounded when enabled.

1. Obtain the matching controller manual and unit/controller/firmware identity.
   Establish baud/parity/data/stop bits, flow control, RTS/DTR, addressing and
   direction, commands/registers, framing/terminators, reads/write, acknowledgements,
   errors, CRC/checksum if used, units/scaling/resolution, timing and side effects.
2. Implement only documented codec behavior in serial.py. Cite source pages and
   add exact byte fixtures. Rerun software acceptance and configure the lab helper.
3. Record the actual interface, wiring, coolant, safe setup and reference instrument.
   Obtain review of that candidate before opening its port.
4. Follow the [hardware procedure](../docs/hardware.md): approved interface checks,
   repeated temperature comparison, setpoint read without changing it, then logging.
5. Only with explicit write authorization, choose one small safe change, send once,
   compare readback, observe physical response and restore the original if approved.
   Record raw replies, configuration and measurements. Never override software limits.

Physical validation is the remaining dependency, not a failed software test.
Polling is not real time; other platforms, adapter electrical behavior and actual
chiller performance remain unvalidated. Forced process termination, uncooperative
OS calls and power loss cannot guarantee cleanup or CSV durability.
