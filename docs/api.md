# Driver, recording and GUI

[Home / quick start](../README.md#hardware-quick-start) · [Hardware setup](hardware.md) · [Development guide](development.md)

Use one TarkChiller per serial port and share it with your experiment code.
The [eight T255P hardware examples](../examples/README.md) provide standalone scripts
and seven notebooks; the dashboard is script-only. Example 05 uses pyserial directly
with the production reply validator; the others construct `TarkChiller`. Review `PORT` in
each example; an unset port raises `ValueError` before port creation. CAL clients
use the explicit API shown below.

Import `TarkChiller` from `tark_chiller.controller`, and `Cal33xx`, `T255P` and
`ProtocolError` from `tark_chiller.protocols`. These are the only import locations;
the controller module provides no protocol compatibility aliases.
Serial settings and optional RS-485 settings are
plain dictionaries, as shown below. Construct
`TarkChiller(settings, protocol=protocol, rs485=mode)` directly.
Each controller owns one pyserial port and its own protocol instance. Native
RS-485 mode does not select the electrical interface; follow the verified
adapter's requirements.
Injected serial factories must return a closed endpoint and must not re-enter
the controller's public methods. One plain I/O lock serializes those operations.

Status, samples and snapshots are plain dictionaries. Read fields with keys,
for example `chiller.collect_status()["connected"]`. The controller owns its
pyserial object, polling worker, history and CSV file directly.
`start_monitoring()` returns `None`; use `chiller.snapshot()` to read its state.
Private `TypedDict` hints describe these fields for editors and type checking;
callers receive ordinary dictionaries and do not construct private record types.
The GUI reads these dictionaries directly. All memory-only simulators live in
`tark_chiller.simulator`; importing or constructing a hardware controller does
not load simulators or Dash. Importing the controller loads the required pyserial
dependency but opens no ports. Opening a hardware connection constructs
`serial.Serial(port=None, ...)`, sets the reviewed RTS/DTR levels and optional
native RS-485 mode, then opens the port.
An injected `serial_factory` is for reviewed in-memory tests; there is no automatic
simulator fallback. Module imports start no workers or device I/O.

For the supported CAL driver, use `Cal33xx(address)` for reads. Enabling writes
requires `Cal33xx(address, allow_writes=True, expected_model=..., expected_firmware=...)`
with actual reviewed identity codes. Do not copy simulator codes into a hardware
profile. [Protocol scope](protocol.md) lists supported models and restrictions.
Only `Cal33xx` and `T255P` protocols are supported; arbitrary protocols are refused
before opening a port.

For T255P use `T255P()` for reads or `T255P(allow_writes=True)` for approved
target/mode/sensor changes, and `TarkChiller(settings, protocol=protocol,
setpoint_range=(5.0, 45.0), coolant_source="ThermoTek T255P Rev C pp9/20; verified water")`.
The protocol independently restricts target writes to 5-45 °C in 0.1 °C steps.
The driver-based tutorials supply this profile; the standalone mode example
does not change targets. Direct clients must explicitly supply
their approved application bounds. The generic TarkChiller default remains the MRC
2-40 °C policy and does not expand automatically to the T255P's full range.
Use the [T255P configuration and first-use procedure](hardware.md#configure-a-t255p).
No remote identity query exists in this subset.

Serial dictionaries require all ten keys shown in the example; there are no
implicit serial or line-level defaults. `TarkChiller` validates values and
copies the dictionaries at construction, before any I/O. Its `settings` and
`rs485` properties return copies, so later caller edits cannot reconfigure a
running connection. Protocol-specific checks also run before opening the port.
An optional `rs485` dictionary requires `rts_level_for_tx`, `rts_level_for_rx`,
`loopback`, `delay_before_tx` and `delay_before_rx`; the delays are seconds or
`None`. Use only settings established for the actual adapter.

<a id="functions"></a>

## Connection and capabilities

| Function or property | Purpose |
| --- | --- |
| `connect()` | Open serial port; CAL checks identity/input/units; T255P sends U, a control-state interaction |
| `disconnect(timeout_s=5)` | Cancel recovery, close connection and join monitoring within the lock/join budget |
| `is_connected` | Check driver connection state; not physical safety |
| `supports_mode_control` | Whether the selected protocol is T255P; no I/O; write opt-in is checked separately |

<a id="t255p-command-api"></a>

## T255P commands: get_ and set_ methods

Getters and the target/sensor setters perform one command/response exchange.
`set_mode()` owns one state-aware job: check U, skip an already-satisfied request,
or send G and verify U readback. These methods require an already connected
T255P and never reconnect or retry, even when read recovery is configured.
Unsupported CAL calls raise `ProtocolError` without sending traffic; use the
checked CAL workflows below.

| Method | Commands | Return / acknowledgement |
| --- | --- | --- |
| `get_temperature()` | I | Manifold Celsius float; does not check J sensor alarms |
| `get_memory()` | H0 | `{"setpoint_c": float, "max_power": int}`; raw read-only power setting, not calibrated watts |
| `get_alarms()` | J | Six named boolean alarm flags |
| `get_status()` | U | Watchdog fields plus `connected`, `backend`, `detail`; no J alarm flags |
| `set_setpoint(value_c)` | M | Check the echoed target; no H readback |
| `set_mode("RUN")` / `set_mode("STANDBY")` | U; conditional G1/G0 and U | Observed status with `mode_command_sent`; verifies real transitions |
| `set_sensor_mode("INTERNAL")` / `set_sensor_mode("EXTERNAL")` | O0 / O1 | Check the qualifier echo |

Modes are `"AUTO START"`, `"STANDBY"`, `"RUN"` and `"SAFETY DEFAULT"`, with
integer `mode_code` 0, 1, 2 and 3. Mode/sensor inputs are case-sensitive; only
RUN/STANDBY are writable. `summary_alarm`, `chiller_on` and `dryer_on` are booleans.
Read the target as `get_memory()["setpoint_c"]` and the mode as
`get_status()["mode"]`. J returns boolean keys `float_switch`, `high_temp`, `low_temp`, `sensor`, `eeprom`
and `watchdog`. No detailed alarm values are inferred from U's summary bit.

Every setter requires `allow_writes=True`; water target bounds still apply.
Target/sensor setters confirm their acknowledgement only. For independent target
readback, call `get_memory()` explicitly or use `apply_setpoint()` below.
`set_mode()` includes its own status verification. The manual documents no sensor-mode query, so there is no
`get_sensor_mode()` or independent O readback. No serial alarm-clear command exists.

`connect()` establishes the session with U. After an idle timeout, explicitly
call `get_status()` before direct H/I/J/M/O operations. `set_mode()` performs
its own U check. Only U establishes or renews an expired session; it can transition AUTO START to RUN. A failed
command closes the transport and is never silently replayed. A failed setter
latches the uncertain write. A separate later getter is its own transaction;
its failure does not retroactively latch an acknowledged setter.

`set_mode()` returns observed status with a boolean `mode_command_sent`.
RUN is already satisfied when U reports RUN or AUTO START with `chiller_on=True`;
STANDBY requires STANDBY with `chiller_on=False`. In those cases only U is sent,
and the actual reported mode is preserved. Inconsistent flags or other modes
still require G and exact mode/on readback. All mode callers use this setter;
there is no separate mode-change wrapper.

## Workflows: explicit coordination

These names deliberately do not start with `get_` or `set_`.

| Method | Exchanges / purpose |
| --- | --- |
| `read_temperature()` | T255P U/J/I with sensor-alarm rejection; CAL checks units/display/PV |
| `read_setpoint()` | T255P U/H0; CAL checks units then reads SP1 |
| `collect_status()` | T255P U/J with combined structured status; CAL identity/configuration/state |
| `apply_setpoint(value_c)` | T255P U/M/H0 with target readback; CAL checks/stages/saves/restarts/verifies |

Each workflow holds serial ownership for the complete sequence. Checked read
workflows can use the configured reconnect/retry budget; write workflows never
replay a write and retain the uncertain-write latch through readback verification.
A direct I getter validates only its reply; use `read_temperature()` to reject a
reported sensor alarm before accepting temperature.

`collect_status()` always returns `connected`, `backend`, `detail`. Successful
T255P results also include all U fields and a nested J `alarms` dictionary.
These same fields appear in successful monitoring samples under `sample["status"]`.
CAL, disconnected and failed-poll status omit the T255P fields; absence does not
mean an inactive alarm. Returned nested dictionaries are detached. CSV retains
its existing columns and human-readable status text.

CAL clients use `apply_setpoint()` instead of the T255P-only `set_setpoint()`.

## Updating existing callers

This revision changes import locations and method contracts without compatibility
aliases. Update application code before reinstalling the checkout:

| Previous use | Current use |
| --- | --- |
| Protocol classes and `ProtocolError` imported from `tark_chiller.controller` | Import from `tark_chiller.protocols`; keep `TarkChiller` in `controller` |
| `chiller.read_status()` | `chiller.collect_status()` for combined diagnostics; T255P `get_status()` sends only U |
| `chiller.set_setpoint(value)` with verified readback | `chiller.apply_setpoint(value)` for either protocol |
| `chiller.run()` / `chiller.standby()` | `chiller.set_mode("RUN")` / `chiller.set_mode("STANDBY")` |
| `examples/06_dashboard.py` | `examples/07_dashboard.py`; example 06 now monitors status in the console or notebook |

T255P `set_setpoint()` now sends M only and clears its uncertain-write latch after
the validated echo. Use `apply_setpoint()` to keep the latch through H0 readback.
CAL does not support the T255P command methods. T255P target and mode requests
now permit reported alarms and target changes in standby; they do not clear
alarms or establish physical readiness. Applications needing additional operating
interlocks must enforce their own reviewed policy before requesting a write.

## Monitoring API

| Function or property | Purpose |
| --- | --- |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Start one worker; returns `None` |
| `running` | Monitoring-thread lifetime; independent of equipment RUN/STANDBY |
| `snapshot()` | Detached history, progress and errors; no device I/O |
| `stop_monitoring(timeout_s=5)` | Join worker and close CSV while keeping the connection |

A TarkChiller context connects on entry and disconnects on exit, including after an
exception. Construction starts no I/O or threads. A second active monitor is
refused. Methods beginning with an underscore are internal;
use TarkChiller's public methods for safe control.

`chiller.running` and `chiller.set_mode("RUN")` describe different things. The property
reports acquisition-thread lifetime; the method requests equipment RUN. Stopping
monitoring does not request STANDBY, and STANDBY does not stop monitoring.

## Monitoring

For live status and temperature output without CSV recording, use the
[monitoring notebook](../examples/06_monitor_status.ipynb) or its matching
[script](../examples/06_monitor_status.py).

After confirming the T255P setup, use the installed package directly:

```python
from time import sleep
from tark_chiller.controller import TarkChiller
from tark_chiller.protocols import T255P

# Replace COM5 and RTS/DTR with your actual port and verified line levels.
settings = {
    "port": "COM5",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "xonxoff": True,
    "rtscts": False,
    "dsrdtr": False,
    "rts": False,
    "dtr": False,
}
with TarkChiller(
    settings,
    protocol=T255P(),
    setpoint_range=(5.0, 45.0),
    coolant_source="ThermoTek T255P Rev C pp9/20; distilled water",
) as chiller:
    # Each getter below sends one command: U, then J.
    status = chiller.get_status()
    alarms = chiller.get_alarms()
    print(status["mode"], status["chiller_on"])
    print(alarms["float_switch"])
    chiller.start_monitoring(interval_s=0.5, csv_path="temperature.csv")
    sleep(3)
    chiller.stop_monitoring()
    snapshot = chiller.snapshot()
    if snapshot["service_error"] or snapshot["logging_error"] or snapshot["failed_samples"]:
        raise RuntimeError(
            snapshot["service_error"] or snapshot["logging_error"] or "A hardware poll failed"
        )
    print(snapshot["sample_count"], snapshot["logged_samples"], snapshot["failed_samples"])
```

`chiller.snapshot()` reads memory only and returns a detached dictionary:

- `history`: a list of sample dictionaries containing time, readings, status and errors.
  The latest sample is `snapshot["history"][-1]` when the list is nonempty.
- `running`, `sample_count`, `failed_samples`: acquisition progress.
- `logging_enabled`: whether a CSV handle is owned, including a failed close.
- `logged_samples`, `logging_error`: recording progress and any recording/close failure.
- `service_error`: an acquisition or shutdown error.
- `poll_in_progress`, `recovery_error`: a pending poll and the current retry reason.

Each sample has `timestamp_utc` (a UTC `datetime`), `elapsed_s`,
`sampled_monotonic` (poll start), `completed_monotonic` (poll completion),
`temperature_c`, `setpoint_c`,
a nested `status` dictionary and `error`. Unavailable fields are `None`. A failed
T255P poll may retain independently validated fields from that same attempt; it
still carries an error and disconnected status. The GUI labels a retained
temperature as historical, never as a live reading. Check failed polls as well
as service/recording errors. History is bounded by `history_size`; that limit does
not truncate CSV. Each successful restart clears history, counters and errors;
later acquisition does not change previously returned snapshots. Editing a snapshot,
its history list, a sample or its nested status cannot change live monitoring
data. Before the first run, `snapshot()` returns an empty stopped state. The interval is read-only; stop and restart monitoring
to change it. A stop timeout can leave work active, including possible CSV cleanup.

The GUI reports age since poll completion and distinguishes Recovering, Waiting
for device, Stale and Monitoring stopped. An active worker with a service error
shows Monitoring fault; Monitoring stopped is reserved for a finished worker.
Poll completion is not a simultaneous physical
measurement timestamp; the sequential readings may have been acquired earlier.

Monitoring shares repeated protocol checks within one poll: CAL uses 13 exchanges
and T255P uses four. Replies are not cached across polls or reused for writes.
Standalone read methods keep their own checks. Buffered serial bytes are read
together; pending reads check disconnect cancellation between read calls, using
a fixed timeout of at most 50 ms per read while
retaining the configured overall transaction timeout. OS/adapter calls and port
close can still take longer; the last read can extend to the end of its 50 ms
slice. Read/write timeouts are configured once before opening the port, avoiding
Windows UART reconfiguration during a transaction.

## Shutdown and cleanup

`stop_monitoring(timeout_s=5)` stops acquisition and joins its worker while keeping
synchronous access available. `disconnect(timeout_s=5)` cancels the connection
intent and read recovery, requests monitoring stop, closes the serial port once I/O
access is available, and joins the worker. Neither sends an equipment stop.

Both require a positive finite timeout. One monotonic deadline covers all
controller lock waits and worker joining; each step receives only the remaining
budget. Concurrent shutdown calls do not gain another full wait at each lock.
Port and CSV close are synchronous: an uncooperative OS driver or filesystem
cannot be preempted and may outlast the budget. No extra shutdown worker is created.

`TimeoutError` means cleanup may be incomplete. An in-flight operation or CSV
close can still finish later; the controller retains ownership. Let pending I/O
make progress, then retry `stop_monitoring()` or `disconnect()` as appropriate.
Failed serial and CSV closes retain their handles for another cleanup attempt.
A serial close failure disables further device use. A CSV close failure sets
`logging_error` and prevents a new recording until cleanup succeeds. Failed
recording is not resumed automatically; check errors even after cleanup succeeds. Do not create a competing controller or replay an uncertain write.

Use a controller context for normal ownership. If its body raises, the same
exception propagates even when disconnect also fails; the cleanup failure is
attached using `BaseException.add_note()`. With no prior exception, a cleanup
failure propagates normally. If serial close and worker joining both fail,
the serial close error remains primary and the join failure becomes a note.
Inspect those notes and retry cleanup; an exception is not proof that resources
were released.

## CSV recording

Supply `csv_path` to `start_monitoring()` as in the
[CSV tutorial](../examples/03_record_csv.ipynb). Each run creates a new file; existing
paths are refused. There is no append.

| Column | Meaning |
| --- | --- |
| `timestamp_utc` | Poll start time in UTC |
| `elapsed_s` | Seconds since monitoring began |
| `temperature_c`, `setpoint_c` | Validated Celsius readings; unavailable fields are blank |
| `backend`, `connected` | Backend label and poll connection state |
| `status` | Device/connection detail |
| `error` | Poll failure, or blank |

Rows are flushed for readers; this is not a power-loss guarantee. CSV failure
sets `logging_error`. Acquisition can continue in the API/GUI without saving new
readings; the CSV tutorial exits on poll, recording or service errors.
`stop_monitoring()` closes CSV while preserving the connection.
`disconnect()` performs monitoring cleanup too.

## Targets and recovery

Defaults are `setpoint_range=(2.0, 40.0)`, `coolant="distilled water"`, with
automatic recovery disabled. Targets must be finite numeric Celsius values;
strings, booleans, NaN, infinity and out-of-range values raise `ValueError`.
No unit conversion occurs. The CAL driver additionally requires RTD/Celsius,
0–40 °C targets, the controller's scale bounds and its 0.1/1 °C resolution.
Custom bounds need a coolant name and `coolant_source`
documenting applicability to the actual setup.

T255P reads signed manifold temperature at 0.01 °C resolution and target at
0.1 °C resolution. Status includes last-reported mode, summary/chiller/dryer bits
and six alarm flags. Sensor alarm invalidates temperature. Reported float-switch
state does not measure flow or establish coolant suitability; flow/leaks remain
unknown. Sensor selection is explicit through `set_sensor_mode()`; sub-zero
target writes remain unsupported.

For a connected controller configured with `T255P(allow_writes=True)`, call
`chiller.set_mode("RUN")` or `chiller.set_mode("STANDBY")`. Both acquire the same I/O lock as polling
and target changes, read U and skip a redundant request when its state already
satisfies the requested operation. Otherwise they send one G, validate its echo,
and check U mode and chiller-on readback. Alarm flags do not veto requests.
They do not change the
target or start/stop monitoring. RUN uses the existing target: review it and
prepare the filled loop first. CAL mode requests raise `ProtocolError` without
sending a command. These are synchronous requests with no automatic retry,
rollback or standby on exit. Physical flow is not measured by mode readback.

T255P checked read/write workflows and monitoring start with U, re-establishing communication after idle
expiry or reconnection. Normal one-second monitoring supplies the manual's
five-second traffic cadence; there is no separate keepalive worker. Arbitrary
poll intervals, process stalls and idle synchronous sessions can relinquish
remote control after >10 s. An open port / `is_connected=True` is transport state,
not an enduring remote lease. Stopping monitoring intentionally stops its traffic.
The next operation may take serial control again, within the reviewed scope.

TarkChiller defaults to 3 s per T255P frame and 1 s for CAL. T255P
preserves a 3 s incomplete-response quarantine on the same controller even if a
shorter timeout is configured. Quarantine, buffer clearing and command echo checks
cannot uniquely identify a valid delayed same-command reply; there are no
transaction IDs. See [protocol limits](protocol.md#thermotek-t255p-rev-c).

Set `reconnect_attempts` to 1–10 for bounded read/connect recovery;
`reconnect_delay_s` defaults to 0.05 s. Each checked read workflow has its own finite recovery
budget.
Read-side I/O failures, checksum-valid T255P communication errors 1/2
(checksum/bad command), and replies with error code zero but the wrong command echo
can use that budget. The rejected payload is always discarded: the driver
closes the port, preserves the 3 s quarantine, reopens, performs a fresh U
handshake, and repeats the whole read/poll. Each retry is logged with its reason;
echo mismatches include the expected command and received frame. Device error
codes take precedence over echo mismatch. Rejected
transactions log exact TX/RX frames. With recovery
disabled, an echo mismatch still suspends the session.
Other protocol errors (including invalid reply checksums, malformed data,
qualifier/unknown error codes and sensor alarms) suspend recovery. Synchronous
read/connect exhaustion requires explicit `connect()`. With read recovery enabled,
monitoring records an exhausted read as a failed poll and attempts a fresh
connection on the next interval, keeping CSV open. Fatal faults still stop the
worker, close CSV and expose their reason in `service_error`.
Transient retries that succeed do not increment `failed_samples`.
After correcting a suspended read-side fault with no uncertain write, call
`stop_monitoring()` to finish cleanup, then `connect()` and `start_monitoring()`.
Use a new CSV path if recording again.
The dashboard script can instead be stopped with Ctrl+C and restarted after the
fault is corrected.
No write is retried or replayed. Resolve an uncertain write by documented readback
before considering another request.

For CAL, an interrupted write latches the controller unusable even across explicit
connect calls. It may have locked the panel or staged/saved a target. Follow the
[human recovery procedure](hardware.md#uncertain-write-or-shutdown); the driver
does not send an automatic exit-program command. Inspect the actual panel and
target before starting a fresh read-only session.

T255P single-command setters latch from M/G/O transmission through their echo
validation. `apply_setpoint()` retains that latch through H readback, and
`set_mode()` through U mode/on readback. O has no independent readback.
A failed reply can mean the target or mode was applied. Neither reconnect nor a new
object authorizes a retry: inspect actual state and follow the T255P recovery
procedure. Disconnect sends no G command and does not stop an operating chiller.

Inspection does not reset the uncertain-write flag in the existing controller
object. It remains unusable for device operations even after explicit reconnect;
follow the hardware recovery procedure before creating a fresh read-only session.

| Error | Meaning |
| --- | --- |
| `ValueError` | Invalid target/configuration |
| `ConnectionError` | Disconnected, cancelled or exhausted recovery |
| `TimeoutError` | Serial I/O or shutdown exceeded its budget |
| `OSError` | Port or recording failure |
| `ProtocolError` | Missing protocol, invalid reply or protocol result |

## GUI

`run_dashboard(chiller, host="127.0.0.1", port=8056)` from `tark_chiller.gui`
serves a standalone Dash app without connecting or starting acquisition.
The [dashboard script](../examples/07_dashboard.py) uses this runner with
`PORT_HTTP = 8006`; open the localhost URL printed at startup. Press
**Connect** at the top right, then **Start monitoring** in the temperature figure
panel. That panel also contains the polling interval and optional CSV path, which
are editable; use a new path for each recording. Each new monitoring run resets
history. The monitoring button toggles to **Stop monitoring**, which joins
acquisition and closes CSV while keeping the connection. **Shutdown GUI**
disconnects and stops the HTTP server; Ctrl+C also
releases resources. Neither shutdown nor disconnect sends a physical standby command.
Connection can trigger T255P AUTO START; prepare the cooling loop first.

The single button under Pump and temperature control shows **STANDBY** when
fresh monitoring data reports the chiller on, and **RUN** when it reports off.
It is disabled without fresh data or mode-change permission. A click submits
the displayed action to `set_mode()`; repeated clicks with the same displayed
action do not reverse it. Failures remain visible; successful requests add no
description. STANDBY leaves acquisition running, and GUI shutdown leaves the
equipment's mode unchanged. Monitoring status contains subsequent mode/alarm diagnostics.

Both `create_app` and `run_dashboard` accept `allow_mode_changes=None`, which
follows `allow_setpoints`. Set `allow_mode_changes=False` to disable mode changes
independently. The callback enforces this policy even for direct HTTP requests;
the CAL mode button is disabled. Driver write opt-in remains required separately.

The runner accepts `interval_s=1.0`, `csv_path=None`, `history_size=3600`,
`stale_after_s=3.0` and `allow_setpoints=True`.
`create_app(chiller)` returns the Dash app for an embedded host with the same
controls. Startup and browser refresh only read snapshots. The shutdown button
is enabled only when the host supplies a thread-safe `on_shutdown()` hook;
the app calls it after disconnect succeeds. The host owns final cleanup on exit.
The CAL and T255P protocols independently enforce their write permissions.

The display shows temperature, reported target, sample age, the poll's UTC start
timestamp, elapsed monitoring time, faults and recording status. Controller
diagnostics are expandable and retain their open state across refreshes.
Equipment status appears below the figure and controls. It reads the structured mode, chiller-on, dryer-on, summary
alarm and six alarm fields from the latest monitoring snapshot. It never parses
diagnostic text or queries the device during refresh. Stopped, stale, recovering,
failed or missing telemetry displays Unavailable; absent alarm flags do not mean
Clear. Alarm states use Clear for no reported alarm and Active for a triggered
alarm; these labels describe alarms, not whether a sensor is operating.
The active-alarm count is shown only when all six flags are available.
The [README screenshot](../README.md#optional-dashboard) shows the current
layout using simulator data.
Drag the graph to zoom, double-click to reset, or select legend entries to hide
traces. Missing readings leave gaps; stale readings are marked.

Apply only approved targets. A successful request does not establish that the
liquid has reached its target. Closing or refreshing the page does not stop or
create acquisition workers; use Shutdown GUI or Ctrl+C to end the standalone host.
