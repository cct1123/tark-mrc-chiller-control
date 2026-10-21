# Driver, recording and GUI

[Home / quick start](../README.md#hardware-quick-start) · [Hardware setup](hardware.md)

Use one TarkChiller per serial port and share it with your experiment code.
The [four hardware tutorials](../examples/README.md) use a shared
[hardware setup module](../examples/hardware.py), separate from the installed API.
Missing model/settings or CAL address raise `ProtocolError` before port creation.

Import `TarkChiller`, `Cal33xx` and `T255P` directly from
`tark_chiller.controller`. Serial settings and optional RS-485 settings are
plain dictionaries, as shown below. Construct
`TarkChiller(settings, protocol=protocol, rs485=mode)` directly.
Each controller owns one pyserial port and its own protocol instance. Native
RS-485 mode does not select the electrical interface; follow the verified
adapter's requirements.
Injected serial factories must return a closed endpoint and must not re-enter
the controller's public methods. One plain I/O lock serializes those operations.

Status, samples and snapshots are plain dictionaries. Read fields with keys,
for example `chiller.read_status()["connected"]`. The controller owns its
pyserial object, polling worker, history and CSV file directly.
`start_monitoring()` returns `None`; use `chiller.snapshot()` to read its state.
The GUI reads these dictionaries directly. All memory-only simulators live in
`tark_chiller.simulator`; importing or constructing a hardware controller does
not load simulators, Dash or pyserial. Opening a hardware connection constructs
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
targets, and `TarkChiller(settings, protocol=protocol,
setpoint_range=(5.0, 45.0), coolant_source="ThermoTek T255P Rev C pp9/20; verified water")`.
The protocol independently restricts writes to 5-45 °C in 0.1 °C steps.
The tutorial setup supplies this profile; direct clients must explicitly supply
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

## Functions

| Function or property | Purpose |
| --- | --- |
| `connect()` | Open serial port; CAL checks identity/input/units; T255P sends U, a control-state interaction |
| `disconnect(timeout_s=5)` | Cancel recovery, close connection and join monitoring within the lock/join budget |
| `is_connected` | Check driver connection state; not physical safety |
| `read_temperature()` | Return a finite Celsius measurement |
| `read_setpoint()` | Return the device's reported Celsius target |
| `set_setpoint(value_c)` | Validate target; CAL saves/restarts; T255P checks state, sends one M and checks echo/H readback |
| `read_status()` | Return a dict with `connected`, `backend` and `detail` |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Start one worker; returns `None` |
| `snapshot()` | Return a detached dict of history, progress and errors; no device query |
| `stop_monitoring(timeout_s=5)` | Join the worker and close CSV, keeping the connection |

A TarkChiller context connects on entry and disconnects on exit, including after an
exception. Construction starts no I/O or threads. A second active monitor is
refused. Methods beginning with an underscore are internal;
use TarkChiller's public methods for safe control.

## Monitoring

After confirming the T255P setup, use the installed package directly:

```python
from time import sleep
from tark_chiller.controller import TarkChiller, T255P

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

Each sample has `timestamp_utc` (a UTC `datetime`), `elapsed_s`,
`sampled_monotonic` (poll start in monotonic seconds), `temperature_c`, `setpoint_c`,
a nested `status` dictionary and `error`. Failed samples have `None` readings and
an `error`. Check failed polls as well
as service/recording errors. History is bounded by `history_size`; that limit does
not truncate CSV. Each successful restart clears history, counters and errors;
later acquisition does not change previously returned snapshots. Editing a snapshot,
its history list, a sample or its nested status cannot change live monitoring
data. Before the first run, `snapshot()` returns an empty stopped state. The interval is read-only; stop and restart monitoring
to change it. A stop timeout can leave work active, including possible CSV cleanup.

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
[CSV tutorial](../examples/03_record_csv.py). Each run creates a new file; existing
paths are refused. There is no append.

| Column | Meaning |
| --- | --- |
| `timestamp_utc` | Poll start time in UTC |
| `elapsed_s` | Seconds since monitoring began |
| `temperature_c`, `setpoint_c` | Celsius readings; blank for failed polls |
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
unknown. Run/standby, sensor selection and sub-zero writes are not exposed.

T255P begins every operation with U, re-establishing communication after idle
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
`reconnect_delay_s` defaults to 0.05 s. Each operation has its own finite recovery
budget.
Protocol errors suspend recovery; exhaustion requires explicit `connect()`.
No write is retried or replayed. Resolve an uncertain write by documented readback
before considering another request.

For CAL, an interrupted write latches the controller unusable even across explicit
connect calls. It may have locked the panel or staged/saved a target. Follow the
[human recovery procedure](hardware.md#uncertain-write-or-shutdown); the driver
does not send an automatic exit-program command. Inspect the actual panel and
target before starting a fresh read-only session.

T255P similarly latches after starting M until echo and H readback both succeed.
A failed reply can mean the target was applied. Neither reconnect nor a new
object authorizes a retry: inspect actual state and follow the T255P recovery
procedure. Disconnect sends no G command and does not stop an operating chiller.

| Error | Meaning |
| --- | --- |
| `ValueError` | Invalid target/configuration |
| `ConnectionError` | Disconnected, cancelled or exhausted recovery |
| `TimeoutError` | Serial I/O or shutdown exceeded its budget |
| `OSError` | Port or recording failure |
| `ProtocolError` | Missing protocol, invalid reply or protocol result |

## GUI

`create_app(chiller)` from `tark_chiller.gui` displays controller snapshots;
it neither connects nor starts acquisition. The
[dashboard tutorial](../examples/04_dashboard.py) owns their lifecycle, disables
the reloader and passes `allow_setpoints=False` for read-only operation. The
CAL and T255P protocols independently enforce their write permissions.

The display shows temperature, reported target, sample age, controller detail, faults and recording
status. The [README screenshot](../README.md#optional-dashboard) illustrates it
using simulator data, not physical measurements.
Drag the graph to zoom, double-click to reset, or select legend entries to hide
traces. Missing readings leave gaps; stale readings are marked.

Apply only approved targets. A successful request does not establish that the
liquid has reached its target. Closing or refreshing the page does not stop or
create acquisition workers; end the host with Ctrl+C in its terminal.
