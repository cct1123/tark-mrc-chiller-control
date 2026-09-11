# Troubleshooting

[Home](../README.md) · [Quick start](quickstart.md) · [Hardware setup](hardware.md)

| Symptom | What to do |
| --- | --- |
| Hardware not configured | Check `examples/connection.py`. Required settings and the documented codec are unavailable until the matching manual is supplied and implemented. No port was opened. |
| Controller communication manual required | Do not guess commands/settings or substitute a test codec. Resolve the documented protocol dependency. |
| Python/install fails | Use Python 3.12+ from the project folder. Check `python --version`. |
| No module named tark_chiller | Install and run with the same environment's Python. |
| No module named examples.connection | Run API snippets from the repository root. Launch numbered scripts with the paths shown in the quick start. |
| Dashboard import fails | Install both `gui` and `serial` extras for the hardware dashboard. |
| Serial port missing/denied/busy | Verify the intended adapter, OS port, permissions and competing applications against the approved setup. |
| Serial timeout or malformed reply | Stop relying on old values. Check the documented setup and connection; read back an uncertain target before considering another write. |
| Target rejected | Use a finite numeric Celsius value inside the approved configured bounds, 2–40 °C by default. |
| Target confirmed but temperature differs | Readback confirms only the requested target. Check the physical temperature and approved operating procedure; do not infer chiller performance from the simulator. |
| Browser cannot connect | Keep the launch terminal open, read its error and use http://127.0.0.1:8050 on that computer. |
| Port 8050 busy | Stop another dashboard instance with Ctrl+C; the example uses port 8050. |
| CSV exists | Edit the script's CSV path to a new filename. Existing data is never overwritten. |
| CSV write/permission error | Check the error, free space and folder permissions. Resolve it before starting a new recording. |
| Recording stopped but temperature updates | Inspect `logging_error`; new readings are not being saved. |
| Unavailable/stale readings | Inspect the fault and terminal output. Resolve the cause before relying on readings. |
| Monitoring already started | Reuse the existing handle or stop it before another run. |
| Stop timeout | Worker or CSV cleanup remains active. Resolve pending I/O and retry; do not assume the file or port is closed. |
| Tab closed but recording continues | Expected: stop with Ctrl+C in the terminal. |

Report the exact command, Python version and full error. State whether the
problem occurred during a fake-serial test or an approved physical run.
The project has no completed physical validation; equipment faults require the
manual and lab procedure.
