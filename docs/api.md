# Python API

[Home](../README.md) · [Hardware setup](hardware.md) · [Examples](../README.md#numbered-hardware-examples)

Use one Chiller per backend and share it with your experiment code. The lab helper
in [examples/connection.py](../examples/connection.py) constructs the serial backend
from explicit settings. It raises `ProtocolUnavailableError` while settings or
the documented codec are missing; it has no simulator fallback.

After configuration and approved read-only validation, run from the repository root:

```python
from examples.connection import create_chiller

with create_chiller() as chiller:
    print(chiller.read_temperature())
    print(chiller.read_setpoint())
    print(chiller.read_status())
```

The context manager connects on entry and disconnects on exit, including after an
exception. Construction and import do not open a port or start monitoring.
All temperatures are Celsius.

## Function layout

| Function or property | What it does | When to use it |
| --- | --- | --- |
| `connect()` | Opens the configured backend without writing a target | Before reads or control |
| `disconnect()` | Cancels recovery, closes the connection, stops monitoring and closes CSV | End of use; not physical power-off |
| `is_connected` | Returns the driver's connection state | Connection check, not safety check |
| `read_temperature()` | Returns a finite Celsius measurement | Synchronous measurement |
| `read_setpoint()` | Returns the device's reported Celsius target | Before and after a target change |
| `set_setpoint(value_c)` | Validates and sends one target request | An approved temperature change |
| `read_status()` | Returns `Status(connected, backend, detail)` | Connection/device diagnostics |
| `start_monitoring(interval_s=1, csv_path=None, history_size=3600)` | Starts one worker and returns its snapshot handle | Optional acquisition/recording |
| `stop_monitoring(timeout_s=5)` | Joins the worker and closes CSV; keeps the connection | Finish recording, then continue direct reads |

Starting a second active monitor raises `RuntimeError`. Use public Chiller
functions; backend methods beginning with an underscore bypass the application
safety checks.

`examples.connection` is an editable helper in this checkout, not an installed
package API or a required integration layer. Other applications can import
`Chiller` from `tark_chiller` and `SerialDevice`, `SerialSettings`, `Codec` and
`RS485Mode` from `tark_chiller.serial`. With their own documented configuration,
they can construct `Chiller(SerialDevice(settings, codec=codec, rs485=mode))`
directly. For RS-232, leave native RS-485 direction mode unset (`None`). For
RS-485, follow the verified adapter's requirements. This option does not select
the electrical interface. No concrete Tark codec is supplied.

For writes, [example 02](../examples/02_set_temperature.py) takes an operator-supplied
Celsius target, reads the original, makes one request and reads it back. There is
no default target to copy into a physical experiment.
Any numeric difference between request and readback is reported as unconfirmed,
without another write. No undocumented rounding tolerance is assumed.

## Monitoring

```python
from time import sleep
from examples.connection import create_chiller

with create_chiller() as chiller:
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
- `service_error`: an acquisition or shutdown error.

Failed samples have missing readings and an `error`. Check both service and
recording errors: acquisition may continue after CSV writing fails. History is
limited to `history_size`; that limit does not truncate CSV. A stop timeout means
work is still active. Resolve it before assuming the file is closed.
The monitor's `interval_s` is read-only. Stop monitoring and start a new run to
change the interval.

See [example 04](../examples/04_monitor_experiment.py) and [CSV details](usage.md#csv-recording).

## Safety and optional recovery

The helper constructs Chiller with its defaults:
`setpoint_range=(2.0, 40.0)`, `coolant="distilled water"`, no automatic recovery.
Strings, booleans, NaN, infinity and out-of-range targets raise `ValueError`
before backend access. No unit conversion is performed.

For another lab-approved coolant, change the Chiller constructor in the helper
with explicit bounds, a coolant name and `coolant_source`. These describe the
operator's policy; they do not detect the fluid or establish safe sub-zero use.

Setting `reconnect_attempts` to an integer from 1 to 10 enables bounded
read/connect recovery; `reconnect_delay_s` defaults to 0.05 s.
A successful read resets the budget. Protocol errors suspend recovery; exhaustion
requires explicit `connect()`. Writes are never retried or replayed.
A write timeout is uncertain: read back the target before deciding on another write.

| Error | Meaning |
| --- | --- |
| `ValueError` | Invalid target or configuration |
| `ConnectionError` | Disconnected, cancelled or exhausted recovery |
| `TimeoutError` | Serial I/O or shutdown exceeded its budget |
| `OSError` | Port or recording failure |
| `ProtocolError` | Invalid reply or codec result |
| `ProtocolUnavailableError` | Required settings or documented codec unavailable |

## Optional GUI

`tark_chiller.gui.create_app(chiller, monitor)` displays an existing driver and
monitoring handle. It does not connect or start acquisition. The host owns
startup and shutdown. Use one server process with its reloader disabled.
[Example 05](../examples/05_launch_dashboard.py) shows this lifecycle for the
configured hardware and records to CSV.
