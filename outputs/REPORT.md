# Engineering report — compact driver 0.2.0

## Objective and outcome

Refactor the project into a small laboratory device driver, usable directly from
scripts and larger experiment-control programs. The software candidate has six
functional modules plus package/module entry files: **13 → 8 Python files,
2,056 → 1,249 lines (39.3% fewer)**. There are no core dependencies, implicit
workers, global registries or compatibility modules.

**187 current tests PASS**, as do static checks, a fresh non-editable installation
and a ten-minute simulator/CSV/Dash run. Real MRC control is unavailable: the
matching controller communication manual is missing and no physical unit has
been validated. No serial hardware was discovered, opened or operated.

## Architecture and API

![Architecture](../docs/assets/system.svg)

`controller.py` provides Chiller, validation, serialized device access and bounded
read recovery. `serial.py` holds explicit configuration, the codec boundary and
byte exchange. `simulator.py` supplies the hardware-free backend. `monitor.py`
combines optional polling, CSV and immutable bounded snapshots. `gui.py` is a thin
optional client; `errors.py` defines only the two protocol exceptions.

```python
from tark_chiller import Chiller, Simulator

with Chiller(Simulator()) as chiller:
    print(chiller.read_temperature())
    chiller.set_setpoint(18.0)
    print(chiller.read_setpoint())
```

The other public operations are connect/disconnect, is_connected, read_status,
start_monitoring and stop_monitoring. Chiller owns one backend and optional
monitor. Stopping monitoring closes CSV but retains the connection; disconnect
also cancels recovery and joins polling. Dash creates neither connection nor
worker; it reads snapshots and submits setpoints through the same API.

Version 0.2 deliberately removes the old get_* names, separate policy/state/logger
objects, CSV append/resume and simulator noise/fault configuration. Each recording
creates a new file exclusively; fault fixtures live only in tests. No old internal
import paths are preserved. [D006](../records/RECORDS.md#d006) records the criterion
changes authorized by prompt 12. [API guide](../docs/api.md).

## Requirements and test evidence

Hardware-independent REQ-001–014 and REQ-016–023 PASS against their updated
criteria. Physical portions of REQ-002/006 and REQ-015 remain BLOCKED by EXT-001/003.
The [current matrix](../STATE.md) maps every requirement; [E029/E030](../records/RECORDS.md#e029)
records review, corrections and evidence. Earlier 0.1 results are historical.

| Validation | Observed result |
| --- | --- |
| Installed unit/integration/fault/example tests | 187 PASS in 29.81 s; [JUnit](driver-tests.xml) |
| Static checks | Ruff lint/format PASS; mypy eight files PASS; pip check PASS |
| Installation | Fresh Python 3.12.14 environment, pinned dependencies, non-editable package |
| Packaging | Source archive/wheel build and module/console entry checks; [build](driver-build.txt), [inventory/integrity](driver-audit.json) |
| Sustained simulator → Chiller → monitor → CSV/Dash | 600 s, 18,445 samples/CSV rows, 14,730 concurrent callbacks, 25 retained samples; [run](driver-soak.txt) |
| Sustained temperature and shutdown | Cooled from 20 °C to 18 °C; acquisition continued without browsers; no worker/file/connection left active |
| Fake serial | Explicit RS232/RS485 setup, unavailable/busy/permission failures, truncated/malformed/delayed replies, timeouts/unplug, bounded recovery and lost-write-acknowledgement tests |
| Browser and guides | Actual screenshot, 18 °C accepted / 1 °C rejected, page reload, responsive layout, 85 local links and five diagrams reviewed |
| GUI shutdown | 2,450 complete rows; 327 measured after browser closure; Ctrl-C stopped sampling; CSV accessible and local ports closed |

Independent review found interrupted startup could leak an open CSV before the
thread started. Ready-event and thread construction now occur within cleanup
protection; regression tests cover these failures and interruption after launch.
Other tests exercise queued-write cancellation across reconnect, one active
worker, pending-read shutdown, disk-full recording failure and fatal worker state.
Acquisition errors produce missing values, never substitute zeros.

The sustained run began before the final startup-interruption guard adjustment.
That adjustment affects failed startup only; the full installed suite, including
successful startup and all interruption regressions, passed on the final source.
The complete successful running path was unchanged. No physical performance is
inferred from simulated temperatures, synthetic messages or callback throughput.

## Exact operation

From the repository root with Python 3.12+ on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install .
.\.venv\Scripts\python examples/01_read_temperature.py
.\.venv\Scripts\python examples/02_set_temperature.py
.\.venv\Scripts\python examples/03_log_temperature.py
.\.venv\Scripts\python -m tark_chiller --headless --duration 60 --csv outputs/run-01.csv
```

For the optional GUI:

```powershell
.\.venv\Scripts\python -m pip install -c requirements-tested.txt ".[gui]"
.\.venv\Scripts\python -m tark_chiller --csv outputs/gui-01.csv
```

Open http://127.0.0.1:8050. Choose a new CSV filename each run. Ctrl-C in the
terminal shuts down the worker, connection and CSV; closing the browser does not.
Use `start_monitoring(csv_path=...)` for recording inside your own application.
The [quick start](../docs/quickstart.md) and [examples](../README.md#numbered-examples)
show the current API. The [development guide](../development/README.md) gives exact
suite, type, build and sustained-test commands.

## Protocol, safety and remaining limitations

The available official [MRC150/300 User Manual Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf)
(E002 source/hash) supports the default distilled-water 2–40 °C envelope on p7.
Page 12 mentions RS232/RS485 while delegating the protocol to a separate controller
manual. Rev 13 also records interface variant changes; applicability to the actual
unit must be checked. The original attachment was unavailable.

No real codec exists. SerialDevice refuses connection before endpoint creation
without a codec. Software timeout/response limits are not claimed Tark settings.
Setpoints must be finite numbers in Celsius within the configured bounds; custom
coolant bounds require an explicit source. Controller acceptance never proves
sub-zero safety. No software telemetry for coolant presence, leaks, flow, level
or alarms is established. Connection status is not physical safety status.

Writes are never retried or replayed. Read recovery is off by default, bounded
when enabled, and suspended by malformed protocol responses. Uncertain writes
require deliberate readback before a new request. A stop timeout means cleanup is
incomplete. Uncooperative OS calls, forced process termination and power loss
cannot guarantee cleanup or CSV durability. Polling is not hard real time;
simulator dynamics are uncalibrated. Other platforms remain untested.

## Exact resumption procedure

1. Obtain the controller manual matching the identified chiller model/suffix,
   controller model and firmware (EXT-001). Extract baud, parity, data/stop bits,
   flow control, RTS/DTR, RS485 addressing/direction, commands/registers,
   framing/terminators, temperature/setpoint reads, setpoint writes,
   acknowledgements/errors, checksum/CRC, units/scaling, response identity,
   timing and side effects.
2. Implement only source-supported codec behavior inside serial.py. Cite each
   command/page and add exact byte fixtures. Re-run the full software suite.
3. Prepare a reviewed candidate with the intended unit/interface, documented
   settings and wiring, installed coolant, safe setup and reference instrument
   (EXT-003). Obtain explicit integration approval under AGENTS.md.
4. Follow the [hardware tutorial](../docs/hardware.md): establish the reviewed
   interface, perform documented read-only status/identification, compare repeated
   temperature and setpoint reads with the front panel/reference.
5. Only with explicit write authorization, send one small safe change, read it
   back, observe temperature and restore the original target if appropriate.
   Record configuration, raw replies, measurements and outcome. Never test
   extremes or override software limits. Shut down software and equipment using
   their respective procedures.

`python examples/06_hardware_configuration.py` currently exits with the precise
missing-protocol message and does not open a serial port. There is no usable
hardware GUI command or supported physical-control claim before these steps.
