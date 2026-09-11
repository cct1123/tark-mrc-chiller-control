# Hardware configuration and validation

[Home / quick start](../README.md#hardware-quick-start) · [API](api.md)

The lab script uses the physical serial backend. It refuses port creation while
the controller manual, settings or documented codec are missing. No physical
MRC150/300 has been validated.

## Confirm the unit

Record the chiller model/suffix, controller model, firmware, installed coolant,
adapter and wiring. Connector shape is not a pinout. Follow the unit's installation
manual and lab procedure for plumbing, power and physical safety checks.

Source: Tark's public [MRC150/300 User Manual, Rev 13](https://tark-solutions.com/sites/default/files/fields/media.file.field_media_file/2024-03/MRC150-300-User-Manual.pdf).
The original attachment was unavailable; compare its revision when supplied.

- Page 12 mentions RS-232/RS-485 and delegates details to a separate controller manual.
- Page 4 removes DH4 RS-485; page 7 lists DH2 RS-232. Confirm the actual interface.
- Page 7 lists distilled water at 2–40 °C, the software default; establish its applicability.

No verified serial signal establishes coolant presence, leaks, flow or level.

![Computer, documented adapter and identified controller](assets/hardware-setup.svg)

## Configure examples/lab.py

| Entry | Value required |
| --- | --- |
| `SERIAL_SETTINGS` | Explicit `SerialSettings` for the verified port/controller |
| `CODEC_CLASS` | Tested class implementing the matching controller protocol |
| `RS485_MODE` | Documented native direction settings if needed by the adapter; otherwise `None` |

`Codec` is an interface, not an implemented Tark protocol. Leave required entries
`None` until verified. `create_chiller()` creates a fresh codec and disconnected
Chiller each time; it never falls back to a simulator.

### SerialSettings fields

Identify the verified adapter's OS port; Windows Device Manager shows names under
**Ports (COM & LPT)**. A port name does not establish device identity.
Use these keyword fields, without copying arbitrary test values:

| Keyword | Meaning / source |
| --- | --- |
| `port` | OS port for the identified adapter |
| `baudrate` | Documented baud rate |
| `bytesize` | Documented number of data bits |
| `parity` | Documented parity |
| `stopbits` | Documented stop-bit setting |
| `xonxoff` | Explicit software flow control |
| `rtscts` | Explicit RTS/CTS flow control |
| `dsrdtr` | Explicit DSR/DTR flow control |
| `rts` | Required initial RTS level |
| `dtr` | Required initial DTR level |

The last five are explicit booleans from controller/adapter documentation.
Configuration and transaction limits are read-only after construction.

### Optional RS-485 mode

`RS485Mode` requires `rts_level_for_tx`, `rts_level_for_rx`, `loopback`,
`delay_before_tx` and `delay_before_rx`. Levels/loopback are explicit booleans;
delays follow the documentation or are `None`. Native support depends on the
OS/adapter. This option does not select the electrical interface or device address.

### Missing protocol details

Obtain baud/parity/data/stop bits, flow control, addressing, commands/registers,
framing/terminators, temperature/setpoint reads, setpoint write, acknowledgements/
errors, checksum/CRC if used, units/scaling, response matching, timing and
read/startup side effects.

Implement and cite each operation behind the codec interface in `serial.py`.
Test exact documented request/reply bytes using memory endpoints before physical
use. The default 1 s timeout and 4096-byte cap are software budgets, not Tark
settings. Port opening can affect RTS/DTR even before transmission.

## Physical validation sequence

Physical work requires the matching protocol, identified setup and reviewed candidate.

| Stage | Action and evidence |
| --- | --- |
| Read-only | Run `lab.py read`. Compare temperature and setpoint with the front panel and approved reference, with matching units. Record settings and request/reply bytes. |
| Logging | Run `lab.py log --csv PATH` for five seconds, then `monitor` if needed. Check UTC times, units, hardware labels and failed rows. Neither changes the target. |
| Approved target change | Run `lab.py set TARGET_C` only after read validation and write approval. It reads the original, writes once and checks exact readback; a mismatch is unconfirmed and never retried. |
| GUI | Run `lab.py gui`, optionally with `--csv PATH`. Check backend, sample age, faults and recording. Apply only approved targets. |
| Shutdown | Exit or press Ctrl+C. The context cancels recovery, stops monitoring and closes CSV/connection. Resolve any stop timeout. |

Even reads may have side effects. Stop on unexpected replies rather than probing
for commands. A reported target does not prove fluid temperature or coolant
suitability. Restore an original target only within the approved scope.
Disconnecting software does not switch off the chiller, pump or cooling; use the
equipment procedure separately. No serial stop command is documented.

The next dependency is the matching communication manual and unit identity.
[Current state](../STATE.md) tracks it.
