# Developing the chiller driver

Start with the memory simulators. They run the production protocol and transport
code without opening a hardware port. Software tests cannot establish electrical
timing, physical flow, calibration or safe operation on equipment.

## Where to make a change

| Change | Start here | Tests to read first |
| --- | --- | --- |
| Parse a device response or change a command sequence | `controller.py`: `T255P` or `Cal33xx` | `test_t255p.py`, `test_cal.py` |
| Change serial deadlines, connection or recovery | `controller.py`: `TarkChiller` | `test_serial.py`, `test_read_recovery.py` |
| Change acquisition, history or CSV | `controller.py`: `_poll`, `_publish`, `_run` | `test_monitor.py` |
| Change the display or a button | `gui.py` | `test_gui.py` |
| Model a device state or inject a fault | `simulator.py` | Protocol tests and `test_communication.py` |

Public callers use `TarkChiller`. Its protocol translates an operation into
commands; its transport sends and validates one frame at a time. The monitor
calls the same protocol code and publishes history. The GUI reads snapshots;
refreshing it must never query the serial port or start another worker.

## Rules to preserve

- Construction opens no port. T255P connection sends U and can start AUTO START.
- Keep one device owner and one serial transaction at a time.
- Validate framing, checksums, echoes and device errors before accepting data.
- Retry only eligible reads. Never retry a write: a lost reply can hide an
  applied target or mode change. Keep the uncertain-write latch until inspection.
- Preserve the T255P incomplete-response quarantine, CAL frame gaps and fresh
  write preflight/readback. Never reuse a poll's cached replies for a write.
- Configure serial timeouts before opening; live timeout changes disturbed the
  retained Windows bench setup.
- Monitoring stop ends acquisition. Disconnect releases software resources.
  Neither commands equipment standby. T255P standby is a separate explicit write.
- If closing a resource fails, keep ownership so cleanup can be retried.

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
