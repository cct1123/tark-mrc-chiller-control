# Driver, recording and GUI

[Home / quick start](../README.md#hardware-quick-start) · [Hardware setup](hardware.md)

Use one TarkChiller per backend and share it with your experiment code.
[examples/lab.py](../examples/lab.py) provides an editable checkout helper,
not an installed API or required integration layer. Missing model/settings or CAL
address raise `ProtocolError` before port creation.

External applications can import `TarkChiller` from `tark_chiller` and
`SerialDevice`, `SerialSettings`, `Cal33xx`, `T255P` and `RS485Mode` from
`tark_chiller.serial`, then construct
`TarkChiller(SerialDevice(settings, codec=codec, rs485=mode))` directly.
Each backend needs its own codec instance. Native RS-485 mode does not select the
electrical interface; follow the verified adapter's requirements.
Backend/codec hooks must not re-enter their owning TarkChiller's public methods;
one plain I/O lock serializes those operations.

For the supported CAL driver, use `Cal33xx(address)` for reads. Enabling writes
requires `Cal33xx(address, allow_writes=True, expected_model=..., expected_firmware=...)`
with actual reviewed identity codes. Do not copy simulator codes into a hardware
profile. [Protocol scope](protocol.md) lists supported models and restrictions.
The generic `Codec` interface remains available for separately reviewed protocols.

For T255P use `T255P()` for reads or `T255P(allow_writes=True)` for approved
targets, and `TarkChiller(SerialDevice(settings, codec=protocol),
setpoint_range=(5.0, 45.0), coolant_source="ThermoTek T255P Rev C pp9/20; verified water")`.
The protocol independently restricts writes to 5-45 °C in 0.1 °C steps.
The lab factory supplies this profile; direct clients must explicitly supply
their approved application bounds. The generic TarkChiller default remains the MRC
2-40 °C policy and does not expand automatically to the T255P's full range.
Use the [T255P configuration and first-use procedure](hardware.md#configure-a-t255p).
No remote identity query exists in this subset.

## Functions

| Function or property | Purpose |
| --- | --- |
| `connect()` | Open backend; CAL checks identity/input/units; T255P sends U, a control-state interaction |
| `disconnect()` | Cancel recovery, close connection, stop monitoring and close CSV |
| `is_connected` | Check driver connection state; not physical safety |
| `read_temperature()` | Return a finite Celsius measurement |
| `read_setpoint()` | Return the device's reported Celsius target |
| `set_setpoint(value_c)` | Validate target; CAL saves/restarts; T255P checks state, sends one M and checks echo/H readback |
| `read_status()` | Return `Status(connected, backend, detail)` |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Start one worker and return its snapshot handle |
| `stop_monitoring(timeout_s=5)` | Join the worker and close CSV, keeping the connection |

A TarkChiller context connects on entry and disconnects on exit, including after an
exception. Construction starts no I/O or threads. A second active monitor is
refused. Backend methods beginning with an underscore are internal I/O hooks;
use TarkChiller's public methods for safe control.

## Monitoring

After confirming the T255P setup, use the installed package directly:

```python
from time import sleep
from tark_chiller import TarkChiller
from tark_chiller.serial import T255P, SerialDevice, SerialSettings

# Replace COM5 and RTS/DTR with your actual port and verified line levels.
settings = SerialSettings(
    port="COM5", baudrate=9600, bytesize=8, parity="N", stopbits=1,
    xonxoff=True, rtscts=False, dsrdtr=False, rts=False, dtr=False,
)
with TarkChiller(
    SerialDevice(settings, codec=T255P()),
    setpoint_range=(5.0, 45.0),
    coolant_source="ThermoTek T255P Rev C pp9/20; distilled water",
) as chiller:
    monitor = chiller.start_monitoring(interval_s=0.5, csv_path="temperature.csv")
    sleep(3)
    chiller.stop_monitoring()
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(snapshot.sample_count, snapshot.logged_samples, snapshot.failed_samples)
```

`monitor.snapshot()` reads memory only. Its immutable result provides:

- `history` and `latest`: samples containing time, readings, status and errors.
- `running`, `sample_count`, `failed_samples`: acquisition progress.
- `logging_enabled`, `logged_samples`, `logging_error`: recording progress.
- `service_error`: an acquisition or shutdown error.

Failed samples have missing readings and an `error`. Check failed polls as well
as service/recording errors. History is bounded by `history_size`; that limit does
not truncate CSV. The interval is read-only; stop and start a new monitor to change
it. A stop timeout means work remains active, including possible CSV cleanup.

## CSV recording

Supply `csv_path` to `start_monitoring()` or `--csv PATH` to a lab recording
command. Each run creates a new file; existing paths are refused. There is no append.

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
readings; the console monitor exits on recording/service errors.
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

SerialDevice defaults to 3 s per T255P frame and 1 s for other codecs. T255P
preserves a 3 s incomplete-response quarantine on the same backend even if a
shorter timeout is configured. Quarantine, buffer clearing and command echo checks
cannot uniquely identify a valid delayed same-command reply; there are no
transaction IDs. See [protocol limits](protocol.md#thermotek-t255p-rev-c).

Set `reconnect_attempts` to 1–10 for bounded read/connect recovery;
`reconnect_delay_s` defaults to 0.05 s. Each operation has its own finite recovery
budget.
Protocol errors suspend recovery; exhaustion requires explicit `connect()`.
No write is retried or replayed. Resolve an uncertain write by documented readback
before considering another request.

For CAL, an interrupted write latches the backend unusable even across explicit
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
| `ProtocolError` | Missing protocol, invalid reply or codec result |

## GUI

`create_app(chiller, monitor)` from `tark_chiller.gui` displays existing objects;
it neither connects nor starts acquisition. The lab script's `gui` command owns
their lifecycle and uses one process with its reloader disabled.
It passes `allow_setpoints=ALLOW_WRITES` to disable target controls during
read-only review. Other clients can pass `allow_setpoints=False` as well; the
CAL and T255P backends independently enforce their write permissions.

The display shows temperature, reported target, sample age, controller detail, faults and recording
status. The [README screenshot](../README.md#optional-dashboard) illustrates it
using simulator data, not physical measurements.
Drag the graph to zoom, double-click to reset, or select legend entries to hide
traces. Missing readings leave gaps; stale readings are marked.

Apply only approved targets. A successful request does not establish that the
liquid has reached its target. Closing or refreshing the page does not stop or
create acquisition workers; end the host with Ctrl+C in its terminal.
