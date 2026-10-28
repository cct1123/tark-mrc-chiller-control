# Developing the chiller driver

Start with the memory simulators. They run the production protocol and transport
code without opening a hardware port. Software tests cannot establish electrical
timing, physical flow, calibration or safe operation on equipment.

## First exercise: read a simulated temperature

Use the [development environment in the README](../README.md#development). Run
this in its Python interpreter:

```python
from tark_chiller.simulator import T255PSimulator

with T255PSimulator().chiller(allow_writes=False) as chiller:
    print(chiller.get_temperature())
    print(chiller.get_status()["backend"])
```

Expected output is `20.0` followed by `simulator`. This uses the production driver
with an in-memory endpoint; it cannot open a hardware port. An injected simulator
clock advances modeled temperature/watchdog state. Serial timeout waits still
use real time, so changing the model clock does not shorten a serial read timeout.

Run one focused test from the repository root:

```sh
uv run --extra gui pytest -q -p no:cacheprovider tests/test_t255p.py::test_malformed_fields_close_session
```

For a first code exercise, add `(b"I", b"+1.60")` to that test's malformed-field
cases. It should still pass: the decimal point is invalid in the fixed-width wire
format, and the test checks that the session closes instead of returning a number.

## Where to make a change

| Change | Start here | Tests to read first |
| --- | --- | --- |
| Parse a device response or change a command sequence | `protocols.py`: `T255P` or `Cal33xx` | `test_t255p.py`, `test_cal.py` |
| Change serial deadlines, connection or recovery | `controller.py`: `TarkChiller` | `test_serial.py`, `test_read_recovery.py` |
| Change acquisition, history or CSV | `controller.py`: `_poll`, `_record_and_publish_sample`, `_run` | `test_monitor.py` |
| Change the display or controls layout | `_views.py` | `test_gui_status.py`, `test_gui.py` |
| Change a button action or dashboard lifecycle | `gui.py` | `test_gui.py` |
| Model a device state or inject a fault | `simulator.py` | Protocol tests and `test_communication.py` |

Public callers use `TarkChiller`. Its protocol translates an operation into
commands; its transport sends and validates one frame at a time. The monitor
calls the same protocol code and publishes history. The GUI reads snapshots;
refreshing it must never query the serial port or start another worker.

To trace a single T255P command, `TarkChiller.get_temperature()` enters
`_t255p_command()`, holds serial ownership and calls `T255P.get_temperature()`.
That sends only I through `_t255p_io()`. There is no handshake, alarm query,
reconnect or retry inside this getter. Other getters and target/sensor setters follow that rule. `set_mode()` owns the state-aware U/(G/U) sequence.

To trace a checked T255P temperature workflow:

1. `TarkChiller.read_temperature()` holds `_io_lock` and calls
   `_read_with_recovery()` with the operation to run.
2. `_call_protocol("read_temperature")` enters `_device_operation()`, which closes failed I/O
   and preserves uncertain writes, then calls `T255P.read_temperature()`.
3. The protocol reads U/J diagnostics and I temperature through `_t255p_io()`.
4. `_t255p_io()` enforces quarantine and validates the frame returned by
   `_transfer()`. The latter sends once and collects bytes within its deadline.

CAL uses the same ownership/recovery path, with `Cal33xx` and `_cal_io()` supplying
its register operations. `_call_protocol()` selects the protocol's I/O function
and invokes the named operation. It replaces separate forwarding methods for
temperature, target, status and verified writes. `collect_status()`,
`apply_setpoint()` and `set_mode()` are explicitly named workflows.
`_control_operation()` shares locking,
cancellation and fault handling for explicit target and mode requests.

The protocol's `io` argument is a function supplied by the controller:
`_t255p_io` accepts command/data bytes, and `_cal_io` accepts function/address/value
integers. The protocol calls that function instead of opening a port itself.
`cache(io)` remembers repeated read replies for one poll; it is discarded after
that poll and never used for writes.

`_read_with_recovery()` receives an `operation` function that takes no arguments.
Calling it runs one complete read attempt; retrying calls it again. A `lambda`
at a call site supplies arguments to the underlying function without running it
yet. This is why writes must never be passed to the recovery function.

`_Status` and the device/read-attempt fields live in `protocols.py`;
`_Sample` and `_Snapshot` live in `controller.py`. They describe the
dictionary fields used across acquisition and the display, using `TypedDict`
for editor/type-checker assistance; values remain ordinary dictionaries. Wire
validation still happens in the protocol and transport. When adding a sample
field, update its schema, `_poll()`, the consumer that needs it, and relevant
tests. CSV columns are an explicit contract in `start_monitoring()` and
`_record_and_publish_sample()`; adding a display field does not automatically
change the CSV.

In `_views.py`, `render_status()` returns the session panel, bottom equipment panel
and available mode action from one snapshot; `render_history()` builds the graph.
Initial layout and refresh call these functions directly. Session actions render
the status panels and mode action without constructing a discarded graph.
`_build_controls()` returns the top-right connection buttons, monitoring controls
inside the figure panel, and the mode/target cards below it. In `gui.py`,
`create_app()` validates options,
builds the app and registers callbacks. The session callback passes the triggering
button ID to `perform_session_action()` explicitly; that helper returns a message
and `disable_refresh`, which refers to browser refresh, not equipment or monitor
state. Timer refresh and session actions both write status/button labels so that
actions can provide immediate feedback; their duplicate outputs are intentional.

## Rules to preserve

- Construction opens no port. T255P connection sends U and can start AUTO START.
- Keep one device owner and one serial transaction at a time.
- Validate framing, checksums, echoes and device errors before accepting data.
- Retry only eligible checked-read workflows. Single-command getters never retry.
  Never retry a write: a lost reply can hide an
  applied target or mode change. An uncertain-write latch stays set in the existing
  controller object, even after inspection or reconnect. Follow the protocol's
  [hardware recovery procedure](hardware.md) before creating a fresh read-only
  session; a new object never authorizes replaying an uncertain write.
- Preserve the T255P incomplete-response quarantine and CAL frame gaps.
  Verified write workflows own preflight/readback; command setters own only their
  acknowledgement. Never reuse poll readings for workflow verification.
- Configure serial timeouts before opening; live timeout changes disturbed the
  retained Windows bench setup.
- Monitoring stop ends acquisition. Disconnect releases software resources.
  Neither commands equipment standby. T255P standby is a separate explicit write.
- If closing a resource fails, keep ownership so cleanup can be retried.

`chiller.running` means the monitoring thread is alive; `chiller.set_mode("RUN")` requests
equipment RUN. Monitoring can be stopped while equipment remains in RUN, or can
continue while equipment is in STANDBY.

Example 05 uses an explicit serial transport. Its initial U/J diagnostics are
followed by `set_mode()`, which performs its own U check and conditional G/U
verification. Controller context entry supplies a startup U as well; compare
traces by individual exchanges rather than assuming identical query counts.

## Readings from a failed attempt

`_attempt_readings` is an ordinary dictionary described by `_AttemptReadings`.
Every key is optional (`total=False`): an attempt may fail before that field is
read. An absent reading is unavailable, never zero. It carries validated fields
between the protocol and the poll's error handler:

1. `_read_with_recovery()` replaces it before every attempt, before any reconnect
   handshake. A failed handshake therefore cannot expose older readings.
2. `_read_sample()` initializes the connection diagnostics. `T255P.sample()` adds
   detail, temperature and target only after each has passed validation.
3. If a later command fails, `_poll()` can record the available fields from that
   final attempt. The row still has an error and disconnected status, and the GUI
   treats its temperature as historical. A retry always starts a new dictionary.

Keep this attempt lifetime separate from `_history`, which stores completed polls
for display and recording. CAL currently returns a complete sample or a failed
poll without retained temperature/target fields.

## Understanding concurrency

`_io_lock` protects serial operations and device fault/write state.
`_monitor_lock` serializes acquisition start/stop and disconnect.
`_data_lock` protects published history and errors; do not perform serial or
filesystem I/O while holding it. Nested acquisition order is monitor, I/O, data.
Snapshot readers only need the data lock, so pending device I/O cannot block them.

`_intent_lock` is held briefly to replace the current cancellation event, then
released before waiting for I/O. Each connection request captures an event.
A newer connect/disconnect sets the previous event, cancelling older requests.
Writes capture their event before waiting for serial access, preventing a queued
write from carrying into a new connection.

`_fault` blocks device use. `_recovery_error` describes a read currently being
retried. `_service_error` records a monitor failure, and `_logging_error` records
CSV failure. Acquisition can continue after a recording failure; callers must
inspect the recording error separately.

## Making a small change

1. Read the existing test for the behavior you want to change. Run it using the
   development environment described in the README.
2. For protocol changes, inject a reply or fault at a memory serial endpoint.
   Assert literal command bytes/order where relevant; driver/simulator agreement
   alone does not prove that both match the manual.
3. Keep validation and cleanup beside the operation they protect. Explain the
   reason for a non-obvious constraint in a comment.
4. Run the affected tests, Ruff and mypy. Run the full suite before committing
   changes to protocol, recovery or ownership behavior.
5. Describe what changed, what was tested, and which hardware claims remain
   unverified. Hardware commissioning is a separate operator activity.

A useful first task is adding a parser rejection test or a display field. Before
changing recovery, be able to explain why reads may retry, writes may not, and
why a cleanup exception does not prove that the port or file was released.

## Choosing a test endpoint

Use `T255PSimulator().chiller()` or `CalSimulator().chiller()` for ordinary API,
GUI, CSV and snapshot tests. These exercise production parsing, frame handling
and cleanup. To test a device fault, change a simulated alarm/register or inject
a reply at the memory endpoint, then assert the controller's public result.

`tests/fakes.py` retains `FakeChiller` for tests needing precise scheduling,
interrupted cleanup, invalid internal return values, or host policy outside the
supported hardware subset. It bypasses the protocol and cannot validate wire
behavior. Keep those focused unit tests separate from application integration.
`FakeSerial` adds fault injection around the CAL memory endpoint while retaining
the production protocol path. Do not remove validation just because a typed
normal-path response already has the expected shape; failure/cleanup behavior
must remain covered.
