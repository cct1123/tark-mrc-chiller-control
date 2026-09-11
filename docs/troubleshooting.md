# Troubleshooting

[Home](../README.md) · [Quick start](quickstart.md) · [User guide](usage.md)

These checks concern the software and simulator. Use the equipment manual and
your lab's procedure for physical chiller problems; this project has no validated
hardware operation yet.

| Symptom | What to check or do |
| --- | --- |
| Python missing or package will not install | Check that Python is 3.12 or newer and PowerShell is in the folder containing `pyproject.toml`. Follow the [quick start](quickstart.md), including its Python version check. |
| No module named tark_chiller or dash | Repeat the quick-start install with `.[gui]`. Use `.\.venv\Scripts\python` to install and run the app. |
| Browser cannot connect | Keep the launching terminal open, check its error message and use http://127.0.0.1:8050 on the same computer. |
| Address/port already in use | If another copy of this app is running, stop it with Ctrl+C in its terminal. The app uses port 8050; it has no command-line option to change that port. |
| CSV already exists | Choose a new filename, or use `--append-csv` to check the existing file and add readings. The app does not overwrite files automatically. |
| Cannot lock CSV / permission denied | Close other programs using the file. Check that you can write to the folder, or choose a new file in a writable local folder. |
| CSV schema mismatch / unterminated record | The file has unexpected columns or a damaged/incomplete row. Keep the original and start a new CSV. The app does not repair damaged files. |
| CSV Failed | Read the file error. Monitoring may still run, but new rows are not being recorded. Fix the storage problem, stop with Ctrl+C and start a new recording. |
| Setpoint rejected | Enter a number in °C within the displayed limits (2–40 °C by default). See [safety and hardware limits](usage.md#safety-and-hardware-limits) before changing a profile. |
| Temperature does not jump to the target | Normal simulator behavior: it approaches the target gradually. Check the reported setpoint on the next update. |
| Last poll: Unavailable | Read the error. After an intentional disconnect, use Connect / retry. Failed polls have blank readings and remain in the CSV. |
| Stale / Monitoring stopped | Read the displayed error and check the terminal. Connect / retry does not restart monitoring. Fix the error, then stop and relaunch the app. |
| Recording continues after tab closure | Expected: monitoring runs separately from the browser. Stop it with Ctrl+C in its terminal. |
| Controller communication manual is required | Real-hardware mode is unavailable. Do not guess serial settings or commands; use the simulator until the matching manual is available and its protocol is implemented. |

When reporting a software issue, include the exact command, Python version,
displayed error and whether it occurred in simulator or fake-serial testing.
Do not describe fake-serial results as physical measurements.
