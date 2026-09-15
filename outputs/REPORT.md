# Engineering report — CAL hardware-test candidate 0.4.0

## Result

Implemented the manufacturer's CAL 3300/9300 commands and a memory-only controller
that uses the same serial path. The software candidate passes review and is
**AWAITING_HUMAN_REVIEW** for physical integration.

**The installed controller has not been identified. No real chiller has been
tested.** The supplied 18-page MRC Rev 13 manual is now accessible and reviewed;
page 14 delegates controller detail and does not identify its manufacturer/model.
Official CAL manuals supply the implemented protocol, but matching menu names
are not proof that your unit contains that controller.

[Protocol evidence and supported subset](../docs/protocol.md) ·
[Human hardware tutorial/checklist](../docs/hardware.md)

## Implementation

- Cal33xx reads model/firmware, RTD/Celsius configuration, temperature, target,
  scale limits, panel lock/resolution, initialization and display/ramp diagnostics.
- Public coolant bounds and controller limits both apply. Writes require exact
  reviewed identity codes and explicit opt-in; lab defaults remain read-only.
- A target change sends the documented five FC06 writes, with checks before and
  after keypad lock, then reads the target back. Exit-program saves and restarts.
- Any interrupted/uncertain write closes and latches the session. No automatic
  retry, unlock, reset, initialization or rollback can apply an unknown target.
- CalSimulator processes those frames, security ordering, staged/committed targets,
  busy/errors and illustrative dynamics. Serial close does not stop modeled
  control. The module launcher's --cal option cannot select physical hardware.
- Dashboard shows controller diagnostic text; read-only lab sessions disable
  target controls and reject direct callback submissions.
- README, hardware tutorial, API, troubleshooting and developer guides are current.
  Superseded screenshot/archive-test output was pruned; historical links point to
  immutable Git revisions. One protocol reference collects sources and limitations.

The six-module design remains: controller.py owns the synchronous API and thermal
model; serial.py owns transport, CAL protocol and memory endpoint; monitor.py
owns optional worker/history/CSV; gui.py owns display/callbacks; two entry files
export/launch. No new runtime dependency or implicit worker was added.

## Validation

| Check | Result / scope |
| --- | --- |
| Existing normal installation | **296 PASS, 32.06 s**; [JUnit](tests.xml) |
| Fresh pinned normal installation | **296 PASS, 32.20 s**, Python 3.12.14 / Windows; [JUnit](fresh-tests.xml) |
| CAL protocol → monitoring → CSV → concurrent Dash | **60 s, 183 rows, 1,167 callbacks**, cap 25, zero failed polls, clean shutdown; [soak](soak.txt) |
| Fresh README Quick Start | Five-second CAL run saved five simulator samples without errors |
| Protocol correctness | Published PV/CRC vector, literal addresses/security sequence, identity/unit/input/limit/lock rejection, every interrupted write stage, post-commit readback failure |
| Static/package checks | Ruff lint/format, mypy six modules, pip check, wheel/source build and installed-source integrity |
| Documentation/GUI | README/API Python blocks through fake serial I/O, rendered diagrams, current CAL screenshot and local links |
| Physical acceptance / calibration | **UNTESTED**; no port discovery/opening or actuation |

[Machine-readable audit](validation.json) contains the candidate source/config/test
hashes and inventory. [Build output](build.txt) records packaging. Older v0.3
results remain historical. Plotly's upstream scattermapbox warning is unrelated
to this application's temperature traces.

## Exact first hardware interactions

The responsible human must confirm CAL 3300/9300 identity and review the actual
MRC interface, wiring, coolant and limits. Record approval for this candidate
before running the first connection. Use the [README commands](../README.md).

With ALLOW_WRITES=False, connection sends four one-register FC03 reads, in order:
**04FC model → 04FD firmware → 0198 input → 0199 unit**. Unsupported replies stop
and close the session. No startup setting or target is written.

Run lab.py read, compare model codes and controller settings with the panel, then
compare temperature/target in °C with the approved reference. Record reference
calibration and a lab-approved acceptance tolerance. Next perform read-only CSV
recording and check failed polls. A software PASS does not supply a physical
temperature tolerance or calibration.

Only after that review: record expected model/firmware codes, approve one explicit
target and the restart side effect, and enable writes. The five writes are
**0300=5 → 1500=0 → 007F=target×10 → 0300=6 → 1600=0**.
Post-lock prerequisite reads occur before staging; target readback follows commit.
Observe keypad recovery, target persistence and the physical response. Restore
the original target only if included in the approval.

## Shutdown, recovery and remaining limits

Ctrl+C stops monitoring and closes CSV/software connection; it does not stop the
physical pump/cooling. There is no implemented power-off command.
An uncertain write can leave a locked keypad or staged/saved settings. Do not
retry or send an exit command blindly. Follow the [human recovery procedure](../docs/hardware.md#uncertain-write-or-shutdown)
and establish panel/target state before a new read-only session.

This candidate supports RTD/Celsius and nonnegative operation only. CAL 9400/9500,
Fahrenheit, linear inputs and negative wire encoding are not enabled. Status codes
do not measure coolant presence, flow, leaks or level. RS485 electrical support,
actual serial/restart timing, physical performance and calibration remain untested.
The simulator is not full firmware or a validated thermal model. RTU response
freshness cannot always be established; OS/USB timing must be checked physically.
Resolve conflicting manufacturer coolant guidance before operation.

See [STATE.md](../STATE.md#human-action-required--next-phase) for the single
resumption request and current requirement evidence.
