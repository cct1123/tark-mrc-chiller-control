# Real-hardware setup and human test

[Quick start](../README.md#hardware-quick-start) · [API](api.md) · [Protocol sources](protocol.md)

**Candidate: 0.4.0, CAL 3300/9300 only. Physical validation is pending.**
The supplied MRC manual is available and has been reviewed. Its page 14 delegates
controller details to a separate manufacturer manual. The CAL guide has now been
obtained and implemented; it does not prove which controller is in your unit.

## Identify and prepare

Record these before any port opening:

| Item | Required evidence |
| --- | --- |
| Chiller | Full MRC model/suffix and serial number |
| Controller | Label showing CAL 3300 or 9300, front-panel firmware, fitted communications option |
| Serial link | Actual RS-232/RS-485 option, approved cable pinout, adapter model/driver and OS port |
| Controller settings | Addr, bAud, dAtA, Inpt=RTD, Unit=C, DISP, Lo.SC, Hi.SC, SP.LK |
| Cooling setup | Coolant/concentration, approved target range, filled/purged circuit, flow/leak checks |
| Review | Candidate revision, operator, approved first reads; writes need separate limits and recovery plan |

The supplied **18-page MRC150/300 Rev 13** gives:

- p6 / p12: upright installation, grounded power, filled circuit and 12 in / 30 cm
  inlet/outlet clearance; follow the complete unit procedure for filling/purging.
- p7: distilled-water control range 2–40 °C; 70/30 water/ethylene-glycol −12–40 °C.
  The CAL candidate does **not** enable sub-zero use.
- p8: **DH2 = RS-232**. The p4 revision history removes the DH4 RS-485 option.
  Page 14's generic RS-485 statement is not confirmation of your interface.
- p14: factory target 10 °C, Hi.SC=40, Lo.SC=0; recommended low limit is
  2 °C above coolant freezing. These are documentation values, not measurements
  of your installed configuration.

**Coolant guidance needs a unit-specific decision:** p7/p12 use a 2 °C boundary;
the [current Tark product page](https://tark-solutions.com/products/thermoelectric-cooler-assemblies/liquid-chiller-mrc-series)
uses 5 °C for distilled-water guidance, and mixture recommendations differ.
Have the responsible lab person/manufacturer resolve this for the actual unit.
Do not treat the software's 2 °C acceptance as authorization to operate there.

![Hardware connection and review](assets/hardware-setup.svg)

## Configure the lab script

Edit the configuration at the top of [examples/lab.py](../examples/lab.py).
It imports SerialSettings, Cal33xx and RS485Mode for you.

SERIAL_SETTINGS must be a SerialSettings(...) instance with every field below:

Configuration template (replace every `...` with a verified value before use):

```python
SERIAL_SETTINGS = SerialSettings(
    port=..., baudrate=..., bytesize=8, parity=..., stopbits=1,
    xonxoff=False, rtscts=False, dsrdtr=False, rts=..., dtr=...,
)
CAL_ADDRESS = ...
ALLOW_WRITES = False
```

| Field | How to choose it |
| --- | --- |
| port | The identified adapter's OS port, e.g. the actual COM name in Windows Device Manager |
| baudrate | Read bAud: documented choices 1200, 2400, 4800, 9600, 19200 |
| bytesize | 8 for this CAL protocol |
| parity | "N", "E" or "O" matching dAtA |
| stopbits | 1 for CAL's documented 8N1 / 8E1 / 8O1 formats |
| xonxoff, rtscts, dsrdtr | False; this candidate does not use flow control |
| rts, dtr | Explicit True / False levels verified for your adapter and wiring |

Set CAL_ADDRESS to the configured address **1–247**. Address zero is forbidden.
The CAL manufacturer's default 9600/8N1 is a reference, **not a measurement of your
unit**; retrofit boards may default to 1200. Check the menu before copying it.
The [communications guide](https://www.west-cs.com/assets/Manuals/CAL-3300-9300-9400-9500-Communications-Manual-English.pdf) describes level C;
it is visible only with a communications board. Do not reset or reinitialize the
controller to inspect it.

Leave ALLOW_WRITES = False, EXPECTED_MODEL = None and EXPECTED_FIRMWARE = None
for the initial approved read. The driver accepts only documented 3300/9300
identity/firmware codes, RTD and Celsius; it changes none of those settings.
A mismatch closes the connection.

After read review, copy the exact printed hex codes to EXPECTED_MODEL and
EXPECTED_FIRMWARE. These are required before ALLOW_WRITES = True. The codes
identify family/output configuration; the physical label is still required.
Do not copy the simulator's codes into a real setup.

RS485_MODE = None is appropriate unless the verified adapter requires native
direction control. RS485Mode(...) requires rts_level_for_tx, rts_level_for_rx,
loopback, delay_before_tx, delay_before_rx; use the adapter's documentation.
OS support varies. This option does not change the electrical interface.

**Cable wiring remains unit-specific.** CAL terminal diagrams do not establish
the MRC enclosure connector pinout. Obtain matching Tark cable/wiring information;
do not connect guessed pins. Port opening may change RTS/DTR levels.

Edits to lab.py apply on the next launch. After editing package source, rerun the
README install command. Use one control process per physical chiller.

## Human hardware test

Record results against **TEST-012**. The lab reviewer must approve this candidate
and actual setup before step 1. Nothing in the automated report authorizes
actuation. Stop on mismatch; do not probe alternative commands/settings.

| Stage | Procedure | Evidence / acceptance |
| --- | --- | --- |
| 1. Read-only connection | Run lab.py read with writes disabled | First frames: FC03 04FC, 04FD, 0198, 0199, one register each. Record matching identity, RTD, Celsius; no FC06 writes. |
| 2. Compare readings | Repeat read; compare PV and target with panel and approved reference | Record time, °C readings, display resolution, reference ID/calibration and lab-approved tolerance. Manual accuracy is not calibration evidence. |
| 3. Read-only logging | Run lab.py log --csv outputs/hardware-check.csv | New file, correct units/identity, increasing timestamps, zero failed polls. |
| 4. Review writes | Confirm SP.LK=OFF, initialized controller, normal display, no ramp/soak; compare DISP and limits | Approve one explicit target, coolant bounds, observation time, stop conditions, restoration target and save/restart behavior. |
| 5. Controlled target | Set expected identity codes, enable writes, run lab.py set TARGET_C | Five FC06 writes: security 5, enter, SP1, security 6, exit. Checks repeated after keypad lock. All echoes valid, exact readback, keypad returns, response within approved conditions. |
| 6. Sustained use | Approved monitor or gui session with a new CSV | Fresh reads, no unexpected faults; compare liquid against reference over the planned interval. |
| 7. Finish | Restore target only if approved; Ctrl+C; equipment shutdown separately | Record final panel target, unit state, closed CSV and software connection. |

Use the README's commands with the virtual environment's Python. TARGET_C is a
placeholder, not a value to copy. Capture command output and reviewed configuration;
if raw bytes are needed, use an approved serial capture method. The application
does not currently save wire traces.

Do not induce physical faults merely to repeat software tests. Real fault injection
needs its own risk review. Actual restart time and serial timing under the selected
OS/adapter need measurement; the default timeout is a software budget of 1 s
**per frame**, not a manufacturer timing guarantee.

## Uncertain write or shutdown

CAL's exit-program command saves changed parameters and restarts the controller.
A missing reply does not show whether a command ran. The driver closes the link,
latches the session unusable and sends **no retry, unlock, reset or rollback**.
Disconnect and process exit do not unlock a controller left in program mode.

1. Stop the experiment using the lab's equipment procedure if conditions require it.
2. Inspect the panel, keypad responsiveness, target and coolant state. Do not assume
   that a visible target is already the active or saved target.
3. Have the responsible operator resolve staged settings under the manufacturer
   procedure. Leaving a menu can apply staged values. Power cycling affects the
   chiller; use it only under the approved recovery procedure.
4. Record the resulting state. Start a **new read-only session** after review;
   creating a new driver object is not itself permission to resume writes.

No hardware test has been performed. Record human results before changing the
[validation status](../STATE.md).
