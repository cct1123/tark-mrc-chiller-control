# Python API

[Home](../README.md) · [Examples](../README.md#numbered-examples) · [Hardware](hardware.md)

Use one `Chiller` per backend and share that instance with your experiment code.
Import and construction do not open a connection or start monitoring.

```python
from tark_chiller import Chiller, Simulator

with Chiller(Simulator()) as chiller:
    print(chiller.read_temperature())
    chiller.set_setpoint(18.0)
    print(chiller.read_setpoint())
```

All temperatures are Celsius. The context manager connects on entry and
disconnects on exit, including when the script raises an exception.

## Function layout

| Function or property | What it does | When to use it |
| --- | --- | --- |
| `connect()` | Opens the backend without writing a target | Before reads or control |
| `disconnect()` | Cancels recovery, closes the connection, stops monitoring and closes CSV | End of use; does not switch physical power off |
| `is_connected` | Returns the driver's connection state | Connection check, not safety check |
| `read_temperature()` | Returns a finite Celsius measurement | Synchronous measurement |
| `read_setpoint()` | Returns the device's reported Celsius target | Before and after a target change |
| `set_setpoint(value_c)` | Validates and sends one target request | A deliberate temperature change |
| `read_status()` | Returns `Status(connected, backend, detail)` | Connection and device diagnostics |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Starts one worker and returns its snapshot handle | Optional acquisition/recording |
| `stop_monitoring(timeout_s=5)` | Joins the worker and closes CSV; keeps the connection | Finish recording, then continue direct reads |

Starting a second monitor while one is active raises `RuntimeError`.
Use Chiller's public functions. Backend methods beginning with an underscore are
internal I/O hooks and bypass application safety checks.

## Monitoring

```python
from time import sleep
from tark_chiller import Chiller, Simulator

with Chiller(Simulator()) as chiller:
    monitor = chiller.start_monitoring(interval_s=0.5, csv_path="temperature.csv")
    sleep(3)
    chiller.stop_monitoring()
    snapshot = monitor.snapshot()
    if snapshot.service_error or snapshot.logging_error:
        raise RuntimeError(snapshot.service_error or snapshot.logging_error)
    print(snapshot.sample_count, snapshot.logged_samples)
```

`monitor.snapshot()` reads memory only. Its immutable result contains:

- `history` and `latest`: samples with time, temperature, target, status and errors.
- `running`, `sample_count` and `failed_samples`: acquisition progress.
- `logging_enabled`, `logged_samples` and `logging_error`: recording progress.
- `service_error`: an unexpected worker failure.

Failed samples have missing temperature/target values and an `error`. Check both
service and recording errors: acquisition can continue after writing fails.
History retains at most `history_size` samples; CSV records the complete run.
A stop timeout means work is still active. Resolve pending I/O and retry
shutdown; do not assume recording has closed.

See [example 04](../examples/04_monitor_experiment.py) and [CSV details](usage.md#csv-recording).

## Limits and recovery

Defaults: `setpoint_range=(2.0, 40.0)`, `coolant="distilled water"`.
Strings, booleans, NaN, infinity and out-of-range targets raise `ValueError`
before backend access. No unit conversion is performed.

Custom bounds require an explicit coolant name and a nonempty `coolant_source`
documenting their applicability. These constructor arguments describe operator
policy; they do not detect fluid or establish safe sub-zero operation.

`reconnect_attempts=0` disables automatic recovery. An integer from 1 to 10
enables bounded read/connect recovery; `reconnect_delay_s` defaults to 0.05 s.
A successful read resets the outage budget. Protocol errors suspend recovery;
exhaustion requires an explicit `connect()`. Writes are never retried or replayed.

| Error | Meaning |
| --- | --- |
| `ValueError` | Invalid target or configuration |
| `ConnectionError` | Disconnected, cancelled or exhausted recovery |
| `TimeoutError` | Serial I/O or monitoring shutdown exceeded its budget |
| `OSError` | Port or recording failure |
| `ProtocolError` | Invalid reply or codec result |
| `ProtocolUnavailableError` | No documented codec configured |

A write timeout has an uncertain outcome. Read back the target before deciding
whether a new write is appropriate.

## Optional GUI

`tark_chiller.gui.create_app(chiller, monitor)` creates a display for an existing
driver and monitoring handle. It does not connect or start acquisition.
The host owns startup/shutdown. Use one server process with its reloader disabled.
The [standard launcher](../examples/05_launch_dashboard.py) manages this lifecycle.

## Updating an older script

Version 0.2 uses `Simulator`, `read_temperature()`, `read_setpoint()` and
`read_status()`. Replace Monitor/CsvLogger/LiveState setup with
`chiller.start_monitoring(csv_path=...)`, and use `monitor.snapshot()`.
CSV append and simulator fault/noise configuration are not part of this API.
