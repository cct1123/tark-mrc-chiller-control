# Dashboard and recording

[Home](../README.md) · [Quick start](quickstart.md) · [API](api.md)

[Example 05](../examples/05_launch_dashboard.py) displays the configured physical
Chiller and records to `outputs/05_dashboard.csv`. It needs the GUI/serial extras,
verified connection configuration and the approved hardware setup. It will not
open a port while the required configuration is missing.

## Dashboard tour

![Actual simulator screenshot, shown only to illustrate the dashboard](assets/dashboard.jpg)

*This screenshot was captured with the simulator. It is not a physical reading
or evidence of validated MRC communication.*

The display shows temperature, reported target, connection/fault information,
sample age and recording status. Apply a target only when that change is
approved. A successful request does not prove the liquid has reached the target;
check the reported value and observe temperature independently.

The graph shows temperature and setpoint versus time. Drag to zoom, double-click
to reset, or use the legend to hide/show a trace. Failed readings leave gaps.
Stale data is marked rather than shown as live.

The script owns the connection and monitoring. Closing or refreshing the page
does not stop or create acquisition workers. End the run with **Ctrl+C in the
launching terminal**.

## CSV recording

Use `chiller.start_monitoring(csv_path="run.csv")` in your experiment.
Examples 03–05 already supply a file path; edit that path for each new run.
Existing filenames are refused. There is no append or automatic overwrite mode.

| Column | Meaning |
| --- | --- |
| `timestamp_utc` | Poll start time in UTC |
| `elapsed_s` | Seconds since monitoring began |
| `temperature_c`, `setpoint_c` | Celsius measurement and reported target; blank on failed polls |
| `backend`, `connected` | Backend label and poll connection state |
| `status` | Device/connection detail |
| `error` | Poll failure, or blank |

Each row is flushed for readers; flushing is not a power-loss guarantee.
CSV failure sets `logging_error`; acquisition can continue in the API/GUI even
though new readings are not saved. The continuous console example exits on
recording or service errors. Inspect failed rows as well as the row count.

`stop_monitoring()` waits for polling and closes CSV while preserving the
connection. `disconnect()` performs this cleanup too.

![Acquisition, CSV and display data flow](assets/data-flow.svg)

## Safe target changes

The default distilled-water range is **2–40 °C**, from the manual's page 7 table.
The lab must establish applicability to the installed coolant and equipment.
All requests are checked before backend access; use numeric Celsius values.

Custom bounds need a documented source. Software cannot identify coolant,
detect leaks or prove sub-zero operation is safe. Writes are never automatically
retried or replayed. See the [hardware guide](hardware.md) for prerequisites and
the staged validation procedure.
