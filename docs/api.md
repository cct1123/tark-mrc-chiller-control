# Python API

[Home](../README.md) · [Quick start](quickstart.md) · [Dashboard](usage.md)

Start with `Chiller(SimulatedDevice())`. The simulator works now. The serial device
shares these operations but still needs a documented protocol implementation.
Use **one Chiller per device**, shared by controls and monitoring.

## Device control

| Call | What it does | When to use it |
| --- | --- | --- |
| `connect()` | Opens the device connection; never sends a target | Before temperature reads or writes |
| `disconnect()` | Closes the software connection; cancels read recovery | At the end of use; does not switch physical power off |
| `is_connected` | Boolean connection property | A quick connection check, not a coolant/safety check |
| `get_temperature()` | Returns a finite Celsius reading | Read the current temperature |
| `get_setpoint()` | Returns the device's reported Celsius target | Check the target, especially after a write |
| `set_setpoint(value_c)` | Validates and sends one target request | Change temperature within the coolant limits |
| `get_status()` | Returns `connected`, `backend` and `detail` | Inspect connection/backend diagnostics, including while disconnected |

Connection and device failures raise `ChillerError` subclasses. Invalid targets
raise `SetpointValidationError` before device access. Use numeric Celsius values;
strings, booleans, NaN and infinity are rejected. No Fahrenheit/Kelvin conversion
is performed. A lost write confirmation is uncertain: read the target before
considering another write. No setpoint write is automatically repeated.

[Read example](../examples/01_read_temperature.py) ·
[Setpoint example](../examples/02_set_temperature.py)

## Monitoring and recording

Import `Chiller`, `SimulatedDevice`, `Monitor` and `CsvLogger` from `tark_chiller`.
Monitoring is independent of the GUI. `Monitor(chiller)` creates its own limited
history; pass `state=LiveState(capacity=120)` only when you need explicit shared state.

| Call or object | What it does | When to use it |
| --- | --- | --- |
| `Monitor(chiller, interval_s=1.0, logger=None)` | Sets up sampling; does not connect or start | Once per experiment |
| `monitor.start()` | Starts one background sampling thread | Continuous monitoring; repeated start does not add a thread |
| `monitor.poll_once()` | Reads and records one sample synchronously | Small scripts; never while the background thread runs |
| `monitor.state.snapshot()` | Returns a consistent copy of history, counters and errors | GUI refresh or experiment progress |
| `monitor.request_stop()` | Stops scheduling new polls | First step of application shutdown |
| `monitor.stop()` | Requests stop and waits for active polling to finish | Before closing the CSV; a timeout raises an error |
| `CsvLogger(path, append=False)` | Opens a new CSV; use `append=True` to validate an existing file | Pass as `logger=` to Monitor |
| `logger.write(sample)` | Writes and flushes one Monitor sample | Only when you intentionally manage recording yourself |
| `logger.rows_written` / `logger.close()` | Counts this session's rows / closes the file | Confirm recording and release the file |

A sample contains UTC time, elapsed seconds, temperature, setpoint, status and an
optional error. Failed polls have blank temperatures, not zero. A snapshot also
reports `running`, `sample_count`, `failed_samples`, `logged_samples`, `service_error`
and `logging_error`. Check both errors: temperature sampling can continue after
CSV recording fails.

Shutdown order is explicit: request stop → disconnect → wait for monitor → close
CSV. If `stop()` times out, keep the logger open until polling has ended. The
[background experiment example](../examples/04_monitor_experiment.py) shows this
order; the [CSV example](../examples/03_log_temperature.py) uses simpler manual polls.

## Optional settings

The default setpoint profile is distilled water, 2–40 °C. A custom `CoolantProfile`
requires a name, finite limits and a documented source, configured on both Chiller
and its device. It is a software limit, not evidence of the installed coolant.

`RecoveryPolicy(max_reconnect_attempts=2)` enables a limited number of read/connect
recovery attempts per outage. The default is zero. Recovery never repeats writes,
and invalid protocol replies stop automatic recovery. A new explicit `connect()`
is required after exhaustion. These are software settings, not Tark serial settings.

For custom dashboards, `tark_chiller.gui.create_app(chiller, monitor.state)` creates
the Dash app only. The caller still owns connection, monitor, CSV and shutdown.
Use one server process with its reloader disabled. The standard launcher already
does this; [example 05](../examples/05_launch_dashboard.py) uses that launcher.
