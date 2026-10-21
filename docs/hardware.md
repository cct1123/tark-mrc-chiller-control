# Hardware setup and first use

[Quick start](../README.md#hardware-quick-start) · [API](api.md) · [Protocol sources](protocol.md)

Both protocols have automated software tests; neither has been tested on a
physical chiller. Verify your equipment and compare initial readings with the
panel before using the driver in an experiment. Use one process per device.
Use the installed Python API directly from your application.

## Configure a T255P

Follow the [Rev C manual](https://neurophysics.ucsd.edu/Manuals/ThermoTek/T255P_chiller_manual.pdf),
especially printed pp6–9,20,23–29. Record model/serial, firmware, coolant, current
panel mode/target, operating limits and adapter/port. There is no model query in
the implemented command subset; a valid ASCII reply cannot identify the unit.

Prepare plumbing and power using the complete manual procedure: upright operation,
at least 6 in air clearance and a filled circuit with suitable coolant. The
implemented target range is distilled water at 5–45 °C. The specified sub-zero
mixture is not supported for writes. Verify flow/leaks independently; the manual's
minimum flow is 0.2 US gpm (0.76 L/min), and an alarm flag does not measure flow.

Use the direct Python example in the README with these settings:

| Setting | Value |
| --- | --- |
| Interface | RS-232, not TTL UART or RS-485 |
| Baud/framing | 9600, 8 data bits, no parity, 1 stop bit |
| Flow control | `xonxoff=True`, `rtscts=False`, `dsrdtr=False` |
| Port / RTS / DTR | Actual port and verified adapter line levels |
| Protocol | `T255P()` initially; target writes disabled |
| Water bounds | `setpoint_range=(5.0, 45.0)` with the documented coolant source |

Port opening can toggle RTS/DTR. Verify the complete cable mapping. The manual's
male DB9 table associates PC receive pin 2 with chiller transmit, PC transmit pin 3
with chiller receive, and pin 5 ground. Connector shape alone does not establish
DTE/DCE compatibility.

## T255P human hardware test

Use this checklist in order. Run one script at a time from the repository root.
These are human-operated hardware tests; software tests do not establish a pass.
Install the package and configure [`examples/hardware.py`](../examples/hardware.py)
using the [T255P tutorial setup](../examples/README.md#t255p-setup).

**Read-only here means no target writes. Connecting sends U, takes serial control
and can change AUTO START to RUN.** Prepare the physical loop for that effect.

### Prepare

- [ ] Confirm the identified T255P, verified RS-232 cable/adapter, filled
  distilled-water loop, ventilation, flow and absence of leaks using the manual.
- [ ] Set `DEVICE_MODEL = "t255p"` and the actual port/RTS/DTR levels in
  `hardware.py`: 9600 baud, 8N1, XON/XOFF enabled; hardware flow control disabled.
  Leave CAL fields and `RS485_MODE` as `None`.
- [ ] Record the tested Git revision, unit/firmware, port/adapter, coolant,
  original panel target and mode. Choose the allowed temperature difference
  against your reference (°C), one approved new target (°C), observation time (s)
  and final equipment state before starting. Do not infer safe limits from an
  example value.

Use the Python environment where the package is installed:

```powershell
$chillerPython = ".\.venv\Scripts\python.exe"
```

### Read-only checks

- [ ] **Read once:** run `& $chillerPython examples/01_read_status.py`.
  Expect `Connected: True`, `Backend: hardware`, finite Celsius readings and
  T255P mode/alarm details. Compare temperature with the panel/reference within
  the chosen tolerance and target with the panel. The target must remain
  unchanged. Record any AUTO START → RUN transition; do not assume every
  starting mode becomes RUN. The script should exit without a cleanup error.
- [ ] **Record a baseline:** run `& $chillerPython examples/03_record_csv.py`
  with its default ten-second duration and one-second interval.
  Expect a new `outputs/hardware_<UTC>.csv`, nonzero samples, saved rows equal to
  samples, increasing timestamps/elapsed time, unchanged target and blank error
  fields. The script must finish without poll, logging or cleanup errors.
  Do not require exactly ten rows; serial query time affects the count.
- [ ] **Optional dashboard:** run `& $chillerPython examples/04_dashboard.py`
  with the GUI extra installed. Open `http://127.0.0.1:8050`.
  Expect fresh readings, an unchanged target, disabled target changes and no
  reported faults. Press Ctrl+C in the terminal and wait for the script to exit
  before continuing. Closing the browser alone does not stop acquisition.

### One setpoint change

- [ ] Proceed only after the read-only checks pass and the panel reports RUN
  with no active alarms. In [`02_set_temperature.py`](../examples/02_set_temperature.py),
  set `TARGET_C` to the approved target, different from the original target,
  within both your setup's limits and 5–45 °C, in 0.1 °C steps.
- [ ] Run `& $chillerPython examples/02_set_temperature.py`. Check the printed
  existing target and requested value before pressing Enter once. Ctrl+C before
  Enter cancels the target request. Expect the printed reported setpoint to
  equal `TARGET_C`, a readback-match message, the same target on the panel and
  a clean exit. The driver sends one M target command and verifies its echo and
  H0 readback; this script does not save a wire trace.
- [ ] Run `03_record_csv.py` again to create a separate post-change CSV. Set
  `DURATION_S` beforehand to your chosen observation time if ten seconds is
  insufficient. Expect the new target in every successful row and no poll or
  recording errors. Compare the temperature response with the panel/reference
  using the chosen tolerance and observation time. Matching target readback
  alone does not demonstrate thermal settling.

### Cleanup and results

- [ ] Wait for each script to exit; controller contexts stop acquisition, close
  CSV and release the serial port on normal exit. If an error or cleanup timeout
  occurs, inspect the exception and cleanup notes; do not assume resources were
  released or start a competing process. Follow the [cleanup procedure](api.md#shutdown-and-cleanup).
- [ ] Record final panel target/mode, CSV paths, observed readings, errors and
  PASS/FAIL for each step. Leave unperformed checks UNTESTED.
- [ ] Follow the manual and your test plan for physical shutdown. **Disconnecting
  software does not stop the chiller/pump or restore the original target.** Any
  necessary target restoration is a separate operator action, outside this one
  setpoint-change test.

Stop the checklist on an unexpected mode, alarm, leak, mismatch or communication
failure. For an uncertain write, inspect the actual panel/target; do not rerun
`02_set_temperature.py` to find out whether the first request succeeded.

The manual calls for valid traffic every five seconds and returns to local control
after more than ten seconds without it. Normal one-second monitoring supplies
traffic, without a timing guarantee or separate keepalive worker. Idle synchronous
use or stopping the monitor can relinquish control; the next operation sends U
again. A running unit continues running after communication is lost.

For an uncertain target write, inspect the panel, target, mode and coolant state.
Wait at least the 3 s response interval before a fresh read-only session; a new
process cannot inherit the old controller's quarantine. Compare the actual target
before any corrective request. Do not blindly retry, power-cycle or induce alarms
as a communication fix.

## Identify and configure CAL

Confirm a CAL **3300 or 9300**, fitted communications option, firmware, RTD input
and Celsius. The MRC manual delegates controller details to the CAL manufacturer
manual; it does not identify the controller in every enclosure. Confirm the actual
coolant, working limits, loop condition, panel target and normal control mode.

Read the address, `bAud` and `dAtA` from the unit's communications menu. Do not reset
or initialize the controller to discover them. Follow the
[CAL communications guide](https://www.west-cs.com/assets/Manuals/CAL-3300-9300-9400-9500-Communications-Manual-English.pdf).

| Setting | Value |
| --- | --- |
| Address | Actual address 1–247; zero is forbidden |
| Baud rate | Actual configured value: 1200, 2400, 4800, 9600 or 19200 |
| Framing | 8 bits, parity N/E/O matching the menu, one stop bit |
| Flow control | XON/XOFF, RTS/CTS and DSR/DTR flow control disabled |
| RTS / DTR | Explicit adapter/wiring levels |
| Protocol | `Cal33xx(address)` initially; writes disabled |
| Application bounds | Default distilled-water policy 2–40 °C |

The manufacturer's default 9600/8N1 is not a measurement of your unit; retrofit
boards may use 1200. CAL terminal diagrams do not establish the MRC enclosure's
connector pinout. Obtain matching cable/wiring information before connecting.

Supply an `rs485` dictionary only for an adapter requiring verified native
direction settings; see the [configuration keys](api.md#functions).
It requires explicit transmit/receive RTS levels, loopback and delays; OS support
varies. This does not convert an RS-232 adapter into RS-485.

## Human hardware test

| Step | Check |
| --- | --- |
| Connect read-only | Use `Cal33xx(address)`. First FC03 reads: model 04FC, firmware 04FD, input 0198, units 0199. Expect supported identity, RTD and Celsius; no FC06 writes. |
| Compare | Check temperature/target against panel and an independent calibrated reference. Record time, units, resolution and tolerance. |
| Record | Start monitoring with a new CSV; check fresh readings and zero failed polls. |
| Prepare writes | Confirm SP.LK=OFF, initialized controller, normal display, no active ramp/soak, DISP resolution and scale limits. Choose a target, observation time and recovery procedure. |
| Set once | Use actual read identity codes as `expected_model`/`expected_firmware` with `allow_writes=True`. Expect security 5 → enter → SP1 → security 6 → exit, exact acknowledgements, target readback and keypad return. Exit saves and restarts the controller. |
| Finish | Observe physical temperature, restore a target only when appropriate, close the context and shut equipment down separately. |

The CAL protocol supports nonnegative readings, RTD/Celsius and 0–40 °C targets
intersected with application bounds. Sub-zero writes are unsupported. The default
response timeout is a software budget of 1 s per frame; actual serial and restart
timing depend on the controller, adapter and OS and need physical measurement.

## Uncertain write or shutdown

Neither protocol replays a write or sends an automatic stop/rollback on failure.
Disconnecting does not stop the chiller or pump. Use the equipment procedure if
conditions require it, then inspect the actual state.

A CAL interruption after the first write may leave the keypad locked, a staged
target or a saved target. The controller closes its port and latches unusable. It does not
send exit-program during cleanup: that command could save the staged value.
Check panel/keypad/target and follow the controller's documented recovery before
creating a new read-only session. Compare target and units before considering
another write. Process exit alone does not unlock the panel.

Record model/firmware, settings, observed commands/results, target/readings in °C,
reference calibration, physical timing and final state for your setup. The driver
does not save raw wire traces. Do not induce physical faults solely to repeat
software tests; memory endpoints cover those error paths.

## Hardware tutorials

The [four tutorials](../examples/README.md) show read/status, one target change,
CSV recording and a read-only dashboard. Enter your verified setup in
[examples/hardware.py](../examples/hardware.py), then run each numbered script.
Only tutorial 02 enables writes; CAL needs actual expected identity codes first.
These tutorials use the installed package's real serial path without a simulator
fallback. Controller contexts close software resources; equipment shutdown
remains a separate physical procedure. If cleanup times out or closing the port
fails, resources may still be owned: allow pending I/O to finish and retry
`disconnect(timeout_s=...)`. A context preserves an existing error and attaches
any cleanup failure as a note. See the [shutdown contract](api.md#shutdown-and-cleanup).
