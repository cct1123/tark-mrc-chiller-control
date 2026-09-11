# Troubleshooting

[Home / quick start](../README.md#hardware-quick-start) · [Hardware setup](hardware.md)

| Symptom | What to do |
| --- | --- |
| Hardware not configured / protocol missing | Supply verified settings and a documented codec in `examples/lab.py`. No port was opened. Do not copy test commands or guess values. |
| Python/install failure | Use Python 3.12+ and install/run with the same environment's Python. |
| No module named examples.lab | Run API snippets from the repository root. This helper is checkout code, not part of the installed package. |
| GUI import failure | Install the `gui` and `serial` extras. |
| Port missing/denied/busy | Verify the intended adapter, OS port, permissions and competing applications against the approved setup. |
| Timeout or malformed reply | Stop relying on old readings. Check the documented connection/settings; resolve uncertain writes by readback. |
| Target rejected or readback unconfirmed | Use an approved finite numeric Celsius target inside configured bounds. A mismatch needs investigation, not a blind retry. |
| Target confirmed but temperature differs | Readback confirms the target only; observe actual temperature independently. |
| Browser cannot connect / port 8050 busy | Read the launch terminal, keep it open and stop another dashboard instance if needed. |
| CSV exists | Choose a new `--csv` path. Existing data is never overwritten. |
| CSV error / recording stopped | Check `logging_error`, free space and permissions. Temperature may still update without recording. |
| Stale or unavailable readings | Read the fault and terminal output; resolve the cause before relying on values. |
| Monitoring already active | Reuse its handle or stop before starting another run. |
| Stop timeout | Worker or file cleanup remains active. Resolve pending I/O and retry; do not assume the file/port is closed. |
| Browser closed but recording continues | Expected: end the host with Ctrl+C in the terminal. |

Report the command, Python version, full error and whether this was a fake-serial
test or an approved physical run. Equipment faults require the manual and lab
procedure; this project has no completed physical validation.
