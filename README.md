# TarkChiller

A Python controller for **ThermoTek T255P** chillers and **CAL 3300/9300**
controllers. Read temperature and setpoint, change targets, and optionally record
CSV or display a dashboard. The public class is `TarkChiller`.

The protocols and simulators are tested in software. **Physical hardware has not
been tested.** Confirm your unit, wiring, serial settings and coolant limits before
connecting. T255P connection takes serial control and can change AUTO START to RUN.

## Install

Requires Python **3.12+**. From this checkout:

```sh
python -m pip install .
```

Or install directly from the repository (requires Git and repository access):

```sh
python -m pip install "git+https://github.com/cct1123/tark-mrc-chiller-control.git"
```

Serial support is included. For the optional dashboard, install `".[gui]"` from
this checkout, or `"tark-mrc-chiller[gui] @ git+https://github.com/cct1123/tark-mrc-chiller-control.git"`.
You can also install a built wheel with `python -m pip install path/to/package.whl`.
Use the Python API directly from your own application.

The source has three Python modules:

| Module | Responsibility |
| --- | --- |
| `tark_chiller.controller` | CAL/T255P protocols, serial settings, device ownership, monitoring and CSV |
| `tark_chiller.gui` | Optional dashboard consuming controller snapshots |
| `tark_chiller.simulator` | Thermal model, memory protocol endpoints and simulator launcher |

Version 0.7 uses explicit module imports and has no compatibility aliases.
`TarkChiller(settings, protocol=...)` owns pyserial, monitoring and recording.
Settings, status, samples and snapshots are plain dictionaries; returned settings
and snapshots are detached copies. Simulators provide `.chiller()` to construct
the same controller with a memory endpoint. Reinstall after upgrading and use the
API shown below.

<a id="hardware-quick-start"></a>

## ThermoTek T255P

Use the actual RS-232 port and verified adapter RTS/DTR levels in place of the
example values below. T255P requires 9600 baud, 8N1 and XON/XOFF. Connect only to a
prepared, filled system: the startup U exchange can start an AUTO START unit.

```python
from tark_chiller.controller import TarkChiller, T255P

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
    print(chiller.read_temperature())  # Celsius
    print(chiller.read_setpoint())
    print(chiller.read_status())
```

`T255P()` disables target writes. After checking the setup and operating limits,
use `T255P(allow_writes=True)` and call `chiller.set_setpoint(target_c)` inside the
context. Water targets are limited to **5–45 °C in 0.1 °C steps**. Run/standby,
sensor selection and sub-zero writes are not exposed. A successful target readback
does not mean the liquid has reached that temperature.

Normal one-second monitoring supplies watchdog traffic. There is no hidden
keepalive worker. After more than ten seconds without valid traffic, the unit can
return to local control; the next API operation sends U again to reacquire it.

## CAL 3300/9300

Match the actual controller address, baud rate, parity and adapter line levels.
The following values illustrate a 9600/8N1 unit at address 1; they are not discovery
results. The driver verifies supported identity codes, RTD input and Celsius.

```python
from tark_chiller.controller import TarkChiller, Cal33xx

settings = {
    "port": "COM5",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "xonxoff": False,
    "rtscts": False,
    "dsrdtr": False,
    "rts": False,
    "dtr": False,
}
with TarkChiller(settings, protocol=Cal33xx(address=1)) as chiller:
    print(chiller.read_temperature())
    print(chiller.read_setpoint())
    print(chiller.read_status())  # Includes model/firmware codes
```

For writes, construct `Cal33xx(address=1, allow_writes=True,
expected_model=..., expected_firmware=...)` using the actual read codes. The
default water profile is **2–40 °C**. CAL writes lock the keypad, stage the target,
then save/restart the controller and check readback. See the
[hardware guide](docs/hardware.md) before enabling them.

## Recording and dashboard

Inside either controller context, use `chiller.start_monitoring(
csv_path="temperature.csv")`. Read `chiller.snapshot()` for bounded history,
latest readings, sample counts and errors. Call `chiller.stop_monitoring()` to
close the CSV and stop acquisition while keeping synchronous access available.
Existing CSV files are refused. Failed samples have blank readings and an error.
See the [API reference](docs/api.md#monitoring) for a complete recording example.

<a id="optional-dashboard"></a>

With the GUI extra installed, pass the controller to
`tark_chiller.gui.create_app(chiller, allow_setpoints=False)` and run the
returned app with `app.run(debug=False, use_reloader=False)`. Browser refresh or
closure does not stop acquisition; exit the host program to close its context.

![Dashboard using T255P simulator data](docs/assets/dashboard.png)

*Simulator screenshot; no physical measurement.*

## Simulation

These commands use memory-only protocol endpoints and never open a hardware port:

```sh
python -m tark_chiller.simulator --t255p --headless --duration 5 --csv demo.csv
python -m tark_chiller.simulator --cal --headless --duration 5
```

Omit `--headless` for the optional dashboard. Omitting both protocol flags selects
the T255P simulator. `tark-chiller` launches the same simulator command.
For Python use, import `CalSimulator` or `T255PSimulator` from
`tark_chiller.simulator` and call `.chiller()` to create a disconnected
`TarkChiller` with a memory-only serial factory. Optional controller arguments
such as `timeout_s` and `reconnect_attempts` can be passed to `.chiller()`.
Simulated dynamics do not predict physical cooling.

## Hardware use and recovery

Use one owning process per device. Construction opens nothing; context entry
connects and context exit stops monitoring, closes CSV and disconnects.
Shutdown lock waits and worker joining share one timeout budget, default five
seconds: `disconnect(timeout_s=...)` or `stop_monitoring(timeout_s=...)`. A timeout
can leave resources owned; let pending I/O finish and retry cleanup. A failed
CSV close also retains its handle for another cleanup attempt. Port and CSV close
are synchronous and can outlast the budget. Contexts preserve an existing exception
and attach cleanup failures as notes. [Shutdown details](docs/api.md#shutdown-and-cleanup).

**Disconnecting software does not stop the chiller or pump.** Follow the equipment
shutdown procedure separately.

Writes are never retried. An uncertain write closes and latches the controller;
inspect the actual panel/target before starting a new read-only session or
considering a corrective target. Alarm flags do not establish flow, leak freedom,
coolant suitability or calibration. Read the [setup and first-use checklist](docs/hardware.md),
[protocol sources and limits](docs/protocol.md), and [API reference](docs/api.md).

The [hardware tutorials](examples/README.md) teach reading status, setting one
target, recording CSV and opening a dashboard with four plain scripts. Configure
[examples/hardware.py](examples/hardware.py) once; direct API use remains independent.

## Development

```sh
python -m pip install -c requirements-tested.txt ".[gui,dev]"
python -m pytest -q -p no:cacheprovider
python -m ruff check src examples tests
python -m ruff format --check src examples tests
python -m mypy
python -m pip check
python -m build
```

Tests use memory transports, including error, cancellation, shutdown, recovery
and no-write-replay paths for both protocols. Normal installation is intentional:
subprocess tests exercise the installed package. Reinstall after source edits.
