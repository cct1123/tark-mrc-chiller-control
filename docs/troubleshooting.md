# Troubleshooting

[Home](../README.md) · [Quick start](quickstart.md) · [Hardware](hardware.md)

| Symptom | What to do |
| --- | --- |
| Python/install fails | Use Python 3.12+ from the project folder. Check `python --version`. |
| No module named tark_chiller | Install and run with the same environment's Python. |
| Dashboard import fails | Install the `gui` extra; the base driver does not include Dash. |
| Browser cannot connect | Keep the launch terminal open, read its error and open http://127.0.0.1:8050 on that computer. |
| Port 8050 busy | Stop another copy with Ctrl+C. The launcher has no port-selection option. |
| CSV exists | Choose a new filename. There is no append or overwrite mode. |
| CSV write/permission error | Check the error, free space and folder permissions. Stop, resolve the problem and choose a new file. |
| Recording stops, temperature updates | Inspect `logging_error`; those new readings are not being saved. |
| Target rejected | Enter a finite numeric Celsius value within the bounds, 2–40 °C by default. |
| Temperature moves slowly | Expected simulator behavior; check the reported target and wait. |
| Unavailable/stale readings | Read the fault and terminal output. Resolve the cause before relying on values again. |
| Monitoring already started | Reuse the handle, or call `stop_monitoring()` before a new run. |
| Stop timeout | Polling is active. Resolve pending I/O and retry shutdown; do not assume CSV is closed. |
| Tab closed but recording continues | Expected: stop with Ctrl+C in the terminal. |
| Controller communication manual required | Hardware is blocked before port opening. Use the simulator; do not guess settings. |
| Future serial port missing/denied/busy | Verify the intended adapter, OS port, permissions and competing applications against the approved setup. |
| Future serial timeout/malformed reply | Stop relying on old data. Check the documented setup; read back an uncertain target before another write. |

When reporting a software issue, include the command, Python version, full error
and whether it used the simulator or a fake-serial test. Use the equipment manual
and approved lab procedure for physical faults; no physical chiller is validated.
