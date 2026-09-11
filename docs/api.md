# Driver, recording and GUI

[Home / quick start](../README.md#hardware-quick-start) · [Hardware setup](hardware.md)

Use one Chiller per backend and share it with your experiment code.
[examples/lab.py](../examples/lab.py) provides an editable checkout helper,
not an installed API or required integration layer. Missing settings or codec
raise `ProtocolError` before port creation.

External applications can import `Chiller` from `tark_chiller` and
`SerialDevice`, `SerialSettings`, `Codec` and `RS485Mode` from
`tark_chiller.serial`, then construct
`Chiller(SerialDevice(settings, codec=codec, rs485=mode))` directly.
Each backend needs its own codec instance. Native RS-485 mode does not select the
electrical interface; follow the verified adapter's requirements.

## Functions

| Function or property | Purpose |
| --- | --- |
| `connect()` | Open the configured backend without writing a target |
| `disconnect()` | Cancel recovery, close connection, stop monitoring and close CSV |
| `is_connected` | Check driver connection state; not physical safety |
| `read_temperature()` | Return a finite Celsius measurement |
| `read_setpoint()` | Return the device's reported Celsius target |
| `set_setpoint(value_c)` | Validate and send one target request |
| `read_status()` | Return `Status(connected, backend, detail)` |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Start one worker and return its snapshot handle |
| `stop_monitoring(timeout_s=5)` | Join the worker and close CSV, keeping the connection |

A Chiller context connects on entry and disconnects on exit, including after an
exception. Construction starts no I/O or threads. A second active monitor is
refused. Backend methods beginning with an underscore are internal I/O hooks;
use Chiller's public methods for safe control.

## Monitoring

After lab configuration and approved read-only validation, run from the repository root:

```python
from time import sleep
from examples.lab import create_chiller

with create_chiller() as chiller:
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
No unit conversion occurs. Custom bounds need a coolant name and `coolant_source`
documenting applicability to the actual setup.

Set `reconnect_attempts` to 1–10 for bounded read/connect recovery;
`reconnect_delay_s` defaults to 0.05 s. A successful read resets the budget.
Protocol errors suspend recovery; exhaustion requires explicit `connect()`.
No write is retried or replayed. Resolve an uncertain write by documented readback
before considering another request.

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

The display shows temperature, reported target, sample age, faults and recording
status. The [README screenshot](../README.md#optional-dashboard) illustrates it
using simulator data, not physical measurements.
Drag the graph to zoom, double-click to reset, or select legend entries to hide
traces. Missing readings leave gaps; stale readings are marked.

Apply only approved targets. A successful request does not establish that the
liquid has reached its target. Closing or refreshing the page does not stop or
create acquisition workers; end the host with Ctrl+C in its terminal.
