"""Hardware tutorials exercised through production protocols and memory endpoints."""

import ast
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tark_chiller.protocols import ProtocolError
from tark_chiller.simulator import CalSimulator, T255PSimulator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TUTORIALS = sorted(path.name for path in EXAMPLES.glob("[0-9][0-9]_*.ipynb"))
SCRIPTS = sorted(path.name for path in EXAMPLES.glob("[0-9][0-9]_*.py"))


def notebook_cells(name):
    notebook = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


@pytest.mark.parametrize("name", TUTORIALS)
def test_notebooks_are_sectioned_and_have_no_sibling_dependencies(name):
    notebook = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4 and notebook["nbformat_minor"] == 5
    cells = notebook["cells"]
    assert len({cell["id"] for cell in cells}) == len(cells)
    headings = "\n".join(
        "".join(cell["source"]) for cell in cells if cell["cell_type"] == "markdown"
    )
    for section in range(1, 6):
        assert f"## {section}." in headings
    for cell in notebook_cells(name):
        tree = ast.parse("".join(cell["source"]))
        compile(tree, name, "exec")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not node.level
                assert node.module.split(".")[0] not in ("hardware", "examples")
            elif isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in ("hardware", "examples") for alias in node.names
                )


def test_examples_have_both_formats_except_script_only_dashboard():
    assert len(TUTORIALS) == 7 and len(SCRIPTS) == 8
    assert SCRIPTS[-1] == "07_dashboard.py"
    assert {Path(name).stem for name in TUTORIALS} == {
        Path(name).stem for name in SCRIPTS if name != "07_dashboard.py"
    }
    assert not (EXAMPLES / "07_dashboard.ipynb").exists()


@pytest.fixture
def configuration():
    return SimpleNamespace(PORT=None)


@pytest.fixture
def hardware(configuration, monkeypatch):
    endpoint = T255PSimulator()
    configuration.PORT = endpoint.chiller().settings["port"]
    monkeypatch.setattr("serial.Serial", endpoint.factory)
    return configuration, endpoint


@pytest.fixture(params=["notebook", "script"])
def run_tutorial(tmp_path, monkeypatch, configuration, request):
    monkeypatch.chdir(tmp_path)

    def run(name, **constants):
        if request.param == "script":
            name = Path(name).with_suffix(".py").name
        namespace = {"__name__": "__main__", "__file__": str(EXAMPLES / name)}
        settings = {"PORT": configuration.PORT}
        settings.update(constants)
        # Execute the script or every notebook code cell in a fresh namespace.
        # Substitute only editable constants; ownership logic runs unchanged.
        cells = (
            notebook_cells(name)
            if name.endswith(".ipynb")
            else [{"source": [(EXAMPLES / name).read_text(encoding="utf-8")]}]
        )
        for cell in cells:
            tree = ast.parse("".join(cell["source"]))
            for node in tree.body:
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AnnAssign)
                    else []
                )
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in settings:
                        key = "_example_" + target.id
                        namespace[key] = settings[target.id]
                        node.value = ast.Name(id=key, ctx=ast.Load())
            exec(compile(ast.fix_missing_locations(tree), name, "exec"), namespace)
        return namespace

    return run


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize("name", TUTORIALS + SCRIPTS)
def test_unconfigured_tutorials_never_open_a_port_or_write_data(name, tmp_path):
    # User-edited examples may contain real ports. Override PORT in both formats
    # and forbid serial construction so tests can never contact bench hardware.
    source = """
import ast
import json
import serial
import sys
from pathlib import Path

class RefuseHardware(serial.Serial):
    def __init__(self, *args, **kwargs):
        raise AssertionError("Unconfigured example attempted serial construction")

serial.Serial = RefuseHardware
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
sources = (["".join(cell["source"]) for cell in json.loads(text)["cells"]
            if cell["cell_type"] == "code"] if path.suffix == ".ipynb" else [text])
namespace = {"__name__": "__main__", "__file__": str(path)}
for source in sources:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PORT" for target in node.targets
        ):
            node.value = ast.Constant(None)
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
"""
    result = subprocess.run(
        [sys.executable, "-c", source, str(EXAMPLES / name)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Hardware not configured" in result.stderr
    assert "No port was opened" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_read_tutorial_uses_real_protocol_and_closes(hardware, run_tutorial, capsys):
    _, endpoint = hardware
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    endpoint.write = capture
    run_tutorial("01_read_status.ipynb")
    output = capsys.readouterr().out
    assert "Temperature:       20.00 C" in output
    assert "Reported setpoint:" in output and "Details:" in output
    assert "Mode:      RUN (code 2)" in output and "sensor: inactive" in output
    assert "Maximum-power setting (raw): 100" in output
    assert requests == [b".U83\r", b".U83\r", b".J78\r", b".I77\r", b".H0A6\r"]
    assert not endpoint.is_open and not endpoint.applied_writes


@pytest.mark.parametrize("devices", [[], ["COM7", "COM3"]])
def test_discovery_lists_ports_without_probing_then_connects_configured_device(
    hardware, run_tutorial, monkeypatch, capsys, devices
):
    configuration, endpoint = hardware

    def discover():
        assert not endpoint.is_open
        return [
            SimpleNamespace(device=device, description="USB adapter", hwid="USB test")
            for device in devices
        ]

    monkeypatch.setattr("serial.tools.list_ports.comports", discover)
    run_tutorial("00_discover_connect.ipynb")
    output = capsys.readouterr().out
    assert "Connecting to configured port: " + configuration.PORT in output
    assert "Connected: True" in output and "Connected after cleanup: False" in output
    if devices:
        assert output.index("COM3: USB adapter") < output.index("COM7: USB adapter")
    else:
        assert "No serial ports reported" in output
    assert not endpoint.is_open and not endpoint.applied_writes


def test_unconfigured_discovery_prints_ports_without_opening_them(
    configuration, run_tutorial, monkeypatch, capsys
):
    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: [SimpleNamespace(device="COM7", description="USB adapter", hwid="USB test")],
    )
    monkeypatch.setattr(
        "serial.Serial", lambda **kwargs: pytest.fail("Discovery opened a serial port")
    )
    with pytest.raises(ValueError, match="No port was opened"):
        run_tutorial("00_discover_connect.ipynb")
    assert "COM7: USB adapter" in capsys.readouterr().out


def test_read_tutorial_preserves_read_error_when_port_close_also_fails(
    hardware, run_tutorial, monkeypatch
):
    configuration, endpoint = hardware
    write, close = endpoint.write, endpoint.close
    primary = ProtocolError("Invalid temperature reply")

    def failed_read(request):
        temperature = request[1:2] == b"I"
        if temperature:
            raise primary
        return write(request)

    def failed_close():
        raise OSError("Port close failed")

    monkeypatch.setattr(endpoint, "write", failed_read)
    monkeypatch.setattr(endpoint, "close", failed_close)
    try:
        with pytest.raises(ProtocolError) as raised:
            run_tutorial("01_read_status.ipynb")
        assert raised.value is primary
        assert any("Disconnect cleanup failed" in note for note in primary.__notes__)
    finally:
        monkeypatch.setattr(endpoint, "close", close)
        endpoint.close()


def test_target_tutorial_applies_one_request(hardware, run_tutorial, monkeypatch, capsys):
    _, endpoint = hardware
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    monkeypatch.setattr(endpoint, "write", capture)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    run_tutorial("02_set_temperature.ipynb", TARGET_C=19.5)
    assert "Reported setpoint: 19.50 C" in capsys.readouterr().out
    assert endpoint.applied_writes == 1 and not endpoint.is_open
    assert [request[1:2] for request in requests] == [b"U", b"H", b"M", b"H"]


def test_read_tutorial_checks_sensor_alarm_before_temperature(hardware, run_tutorial, monkeypatch):
    _, endpoint = hardware
    endpoint.alarms = "000100"
    write = endpoint.write

    def forbid_temperature(request):
        assert request[1:2] != b"I", (
            "Example accepted temperature without checking the sensor alarm"
        )
        return write(request)

    monkeypatch.setattr(endpoint, "write", forbid_temperature)
    with pytest.raises(ProtocolError, match="sensor alarm"):
        run_tutorial("01_read_status.ipynb")
    assert not endpoint.is_open


@pytest.mark.parametrize("fault", ["mismatch", "timeout"])
def test_target_readback_failure_does_not_replay_acknowledged_write(
    hardware, run_tutorial, monkeypatch, capsys, fault
):
    _, endpoint = hardware
    write = endpoint.write
    commands = []

    def fail_readback(request):
        commands.append(request[1:2])
        count = write(request)
        if request[1:2] == b"H" and endpoint.applied_writes:
            if fault == "timeout":
                raise TimeoutError("Readback timeout after M acknowledgement")
            body = b"#H00+180,100"
            endpoint._buffer = body + f"{sum(body) & 0xFF:02X}\r".encode("ascii")
        return count

    monkeypatch.setattr(endpoint, "write", fail_readback)
    with pytest.raises(TimeoutError if fault == "timeout" else ProtocolError) as raised:
        run_tutorial("02_set_temperature.ipynb", TARGET_C=19.5)
    assert "Target acknowledgement received" in capsys.readouterr().out
    if fault == "timeout":
        assert any("do not replay M" in note for note in raised.value.__notes__)
    assert commands == [b"U", b"H", b"M", b"H"]
    assert endpoint.applied_writes == 1 and not endpoint.is_open


@pytest.mark.parametrize(
    "mode, expected, frame",
    [
        ("STANDBY", "1", b".G0A5\r"),
        ("RUN", "2", b".G1A6\r"),
    ],
)
def test_mode_tutorial_sends_one_published_request_and_closes(
    hardware, run_tutorial, monkeypatch, capsys, mode, expected, frame
):
    _, endpoint = hardware
    endpoint.mode = "1" if mode == "RUN" else "2"
    requests = []
    write = endpoint.write

    def capture(request):
        requests.append(request)
        return write(request)

    monkeypatch.setattr(endpoint, "write", capture)
    monkeypatch.setattr("builtins.input", lambda prompt: mode)
    run_tutorial("05_run_standby.ipynb", MODE=mode)
    assert requests == [b".U83\r", b".J78\r", b".U83\r", frame, b".U83\r"]
    assert f"Reported {mode} matches" in capsys.readouterr().out
    assert endpoint.mode == expected and not endpoint.is_open
    assert not endpoint.applied_writes  # No temperature target writes.


@pytest.mark.parametrize(
    "requested,reply,reported",
    [
        ("RUN", b"#U0001069\r", "AUTO START"),
        ("RUN", b"#U020106B\r", "RUN"),
        ("STANDBY", b"#U0100069\r", "STANDBY"),
    ],
)
def test_mode_tutorial_skips_redundant_g_and_preserves_actual_reported_state(
    hardware, run_tutorial, monkeypatch, capsys, requested, reply, reported
):
    _, endpoint = hardware
    requests = []
    write = endpoint.write
    respond = endpoint._respond

    def emulate_recorded_unit(request):
        if request[1:2] == b"U":
            respond(request)  # Establish the simulator session before overriding its wire status.
            return reply
        if request[1:2] == b"G":
            return b"#G31CE\r"
        return respond(request)

    def capture(request):
        requests.append(request)
        return write(request)

    monkeypatch.setattr(endpoint, "_respond", emulate_recorded_unit)
    monkeypatch.setattr(endpoint, "write", capture)
    monkeypatch.setattr("builtins.input", lambda prompt: requested)
    run_tutorial("05_run_standby.ipynb", MODE=requested)
    output = capsys.readouterr().out
    assert "request already satisfied" in output and "No G command sent" in output
    assert f"reported mode={reported}" in output
    assert requests == [b".U83\r", b".J78\r", b".U83\r"]
    assert not endpoint.is_open and not endpoint.applied_writes


@pytest.mark.parametrize("answer", ["", "run", "NO"])
def test_mode_tutorial_cancel_does_not_open_port(hardware, run_tutorial, monkeypatch, answer):
    _, endpoint = hardware
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    monkeypatch.setattr(endpoint, "open", lambda: pytest.fail("Cancelled example opened port"))
    with pytest.raises(ValueError, match="Cancelled"):
        run_tutorial("05_run_standby.ipynb")


@pytest.mark.parametrize("mode", ["RUN", "STANDBY"])
def test_mode_tutorial_reports_alarms_without_vetoing_mode_request(
    hardware, run_tutorial, monkeypatch, mode
):
    _, endpoint = hardware
    endpoint.mode, endpoint.alarms = "2", "010000"
    monkeypatch.setattr("builtins.input", lambda prompt: mode)
    run_tutorial("05_run_standby.ipynb", MODE=mode)
    assert endpoint.mode == ("2" if mode == "RUN" else "1")
    assert endpoint.alarms == "010000"
    assert not endpoint.is_open


@pytest.mark.parametrize("fault", ["lost_ack", "wrong_echo", "wrong_mode", "bad_checksum"])
def test_mode_tutorial_uncertain_result_is_not_replayed(hardware, run_tutorial, monkeypatch, fault):
    _, endpoint = hardware
    write = endpoint.write
    commits = []

    def fail_after_commit(request):
        count = write(request)
        if request[1:2] == b"G":
            commits.append(request)
            if fault == "lost_ack":
                raise TimeoutError("Lost mode acknowledgement")
            if fault == "wrong_echo":
                endpoint._buffer = b"#G01CB\r"
            if fault == "wrong_mode":
                endpoint.mode = "2"
            if fault == "bad_checksum":
                endpoint._buffer = b"#G0000\r"
        return count

    monkeypatch.setattr(endpoint, "write", fail_after_commit)
    monkeypatch.setattr("builtins.input", lambda prompt: "STANDBY")
    with pytest.raises((ProtocolError, TimeoutError)) as raised:
        run_tutorial("05_run_standby.ipynb", MODE="STANDBY")
    assert any("do not replay" in note for note in raised.value.__notes__)
    assert commits == [b".G0A5\r"] and not endpoint.is_open


@pytest.mark.parametrize("target", [float("nan"), float("inf"), True, -1, 46])
def test_target_tutorial_keeps_public_value_guards(hardware, run_tutorial, monkeypatch, target):
    _, endpoint = hardware
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(ValueError):
        run_tutorial("02_set_temperature.ipynb", TARGET_C=target)
    assert not endpoint.is_open and not endpoint.applied_writes


def test_target_tutorial_uses_configured_target_without_extra_prompt(
    hardware, run_tutorial, monkeypatch
):
    _, endpoint = hardware

    def unexpected_prompt(prompt):
        pytest.fail("The configured target operation should not prompt again")

    monkeypatch.setattr("builtins.input", unexpected_prompt)
    run_tutorial("02_set_temperature.ipynb")
    assert not endpoint.is_open and endpoint.applied_writes == 1


def test_uncertain_target_is_never_replayed(hardware, run_tutorial, monkeypatch):
    configuration, endpoint = hardware
    write = endpoint.write
    commits = []

    def lost_ack(request):
        count = write(request)
        commit = request[1:2] == b"M"
        if commit:
            commits.append(request)
            raise TimeoutError("Lost write acknowledgement")
        return count

    monkeypatch.setattr(endpoint, "write", lost_ack)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(ProtocolError, match="uncertain"):
        run_tutorial("02_set_temperature.ipynb", TARGET_C=19.5)
    assert len(commits) == endpoint.applied_writes == 1
    assert not endpoint.is_open


def test_csv_tutorial_records_and_refuses_existing_file(hardware, run_tutorial, tmp_path, capsys):
    _, endpoint = hardware
    constants = dict(DURATION_S=0.1, INTERVAL_S=0.01, stamp="test")
    run_tutorial("03_record_csv.ipynb", **constants)
    path = tmp_path / "outputs/hardware_test.csv"
    samples = rows(path)
    assert samples and all(row["temperature_c"] and not row["error"] for row in samples)
    assert "saved rows:" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        run_tutorial("03_record_csv.ipynb", **constants)
    assert path.read_bytes() == before
    assert not endpoint.is_open and not endpoint.applied_writes
    path.unlink()  # Closed writer handle, including on Windows.


@pytest.mark.parametrize("device_mode", ["1", "2"])
def test_status_monitor_prints_live_fields_without_writes_or_csv(
    hardware, run_tutorial, monkeypatch, tmp_path, capsys, device_mode
):
    _, endpoint = hardware
    endpoint.mode = device_mode
    commands = []
    write = endpoint.write

    def capture(request):
        commands.append(request[1:2])
        return write(request)

    monkeypatch.setattr(endpoint, "write", capture)
    result = run_tutorial("06_monitor_status.ipynb", DURATION_S=0.05, INTERVAL_S=0.01)
    output = capsys.readouterr().out
    assert "20.00 C" in output and "target=20.00 C" in output
    expected_mode, expected_on = ("RUN", "On") if device_mode == "2" else ("STANDBY", "Off")
    assert f"mode={expected_mode}" in output and f"chiller={expected_on}" in output
    assert "dryer=Off" in output
    assert "summary_alarm=False" in output and "active alarms: none" in output
    assert "Monitoring complete:" in output and "no CSV written" in output
    assert set(commands) == {b"U", b"J", b"I", b"H"}
    assert not endpoint.applied_writes and not endpoint.is_open
    assert not result["chiller"].running and not result["chiller"].is_connected
    assert list(tmp_path.iterdir()) == []


def test_status_monitor_labels_sensor_failure_unavailable_and_cleans_up(
    hardware, run_tutorial, capsys
):
    _, endpoint = hardware
    endpoint.alarms = "000100"
    with pytest.raises(RuntimeError, match="sensor alarm|monitoring poll failed"):
        run_tutorial("06_monitor_status.ipynb", DURATION_S=0.05, INTERVAL_S=0.01)
    output = capsys.readouterr().out
    assert "UNAVAILABLE" in output and "sensor alarm" in output
    assert "20.00 C" not in output
    assert not endpoint.is_open and not endpoint.applied_writes


def test_status_monitor_can_run_until_interrupt_and_releases_the_worker(
    hardware, run_tutorial, monkeypatch
):
    _, endpoint = hardware

    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("06_monitor_status.ipynb", DURATION_S=None)
    assert not endpoint.is_open and not endpoint.applied_writes


@pytest.mark.parametrize(
    "option,value",
    [
        ("DURATION_S", 0),
        ("DURATION_S", float("nan")),
        ("INTERVAL_S", 0),
    ],
)
def test_invalid_status_monitor_options_never_connect(
    hardware, run_tutorial, monkeypatch, option, value
):
    _, endpoint = hardware
    monkeypatch.setattr(endpoint, "open", lambda: pytest.fail("Invalid options opened the port"))
    with pytest.raises(ValueError, match=option):
        run_tutorial("06_monitor_status.ipynb", **{option: value})


def test_csv_tutorial_reports_a_poll_failure(hardware, run_tutorial, monkeypatch):
    configuration, endpoint = hardware
    write = endpoint.write

    def disconnected_during_temperature_read(request):
        temperature = request[1:2] == b"I"
        if temperature:
            raise OSError("Test endpoint disconnected")
        return write(request)

    monkeypatch.setattr(endpoint, "write", disconnected_during_temperature_read)
    with pytest.raises(RuntimeError, match="poll failed"):
        run_tutorial("03_record_csv.ipynb", DURATION_S=0.1, INTERVAL_S=0.01)
    assert not endpoint.is_open and not endpoint.applied_writes


def test_csv_tutorial_ctrl_c_closes_worker_file_and_port(
    hardware, run_tutorial, monkeypatch, tmp_path
):
    _, endpoint = hardware

    def cancel(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", cancel)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("03_record_csv.ipynb", stamp="cancelled")
    assert not endpoint.is_open and not endpoint.applied_writes
    (tmp_path / "outputs/hardware_cancelled.csv").unlink()


@pytest.mark.parametrize("run_tutorial", ["script"], indirect=True)
def test_dashboard_tutorial_allows_one_target_and_joins_on_ctrl_c(
    hardware, run_tutorial, monkeypatch, tmp_path
):
    _, endpoint = hardware
    closed = []
    http_port = 8123

    def check_app(app):
        assert app.test_client().get("/").status_code == 200
        layout = app.test_client().get("/_dash-layout").get_data(as_text=True)
        assert "Read-only session" not in layout

    def run(app):
        check_app(app)
        assert not endpoint.is_open  # Opening the dashboard does not connect.
        output = next(
            callback["output"]
            for callback in app.test_client().get("/_dash-dependencies").json
            if "session-result" in callback["output"]
        )
        for action in ("connect-device", "toggle-monitoring"):
            response = app.test_client().post(
                "/_dash-update-component",
                json={
                    "output": output,
                    "outputs": [
                        {"id": "session-result", "property": "children"},
                        {"id": "refresh", "property": "disabled"},
                        {"id": "live-status", "property": "children"},
                        {"id": "toggle-monitoring", "property": "children"},
                        {"id": "equipment-panel", "property": "children"},
                        {"id": "toggle-mode", "property": "children"},
                        {"id": "toggle-mode", "property": "disabled"},
                    ],
                    "inputs": [
                        {"id": name, "property": "n_clicks", "value": int(name == action)}
                        for name in ("connect-device", "toggle-monitoring", "shutdown-gui")
                    ],
                    "state": [
                        {"id": "monitor-interval", "property": "value", "value": 0.01},
                        {
                            "id": "monitor-csv",
                            "property": "value",
                            "value": "outputs/dashboard.csv",
                        },
                    ],
                    "changedPropIds": [f"{action}.n_clicks"],
                },
            )
            assert response.status_code == 200 and "failed" not in response.get_data(as_text=True)
        assert endpoint.is_open
        response = app.test_client().post(
            "/_dash-update-component",
            json={
                "output": "setpoint-result.children",
                "outputs": {"id": "setpoint-result", "property": "children"},
                "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
                "state": [{"id": "setpoint-input", "property": "value", "value": 19.0}],
                "changedPropIds": ["apply-setpoint.n_clicks"],
            },
        )
        assert response.status_code == 200
        assert response.json["response"]["setpoint-result"]["children"] == ""
        raise KeyboardInterrupt

    def make_server(host, port, app, *, threaded):
        assert (host, port, threaded) == ("127.0.0.1", http_port, True)
        return SimpleNamespace(
            serve_forever=lambda: run(app), server_close=lambda: closed.append(True)
        )

    monkeypatch.setattr("tark_chiller.gui.make_server", make_server)
    result = run_tutorial(
        "07_dashboard.py", PORT_HTTP=http_port, CSV_PATH="outputs/dashboard.csv", INTERVAL_S=0.01
    )
    assert closed == [True]
    assert not result["chiller"].snapshot()["running"]
    assert not endpoint.is_open and endpoint.applied_writes == 1
    path = tmp_path / "outputs/dashboard.csv"
    assert path.is_file()
    path.unlink()


def test_timing_notebook_connects_and_releases(hardware, run_tutorial, capsys):
    _, endpoint = hardware
    result = run_tutorial("04_connection_timing.ipynb")
    output = capsys.readouterr().out
    assert "connect():" in output and "disconnect():" in output
    assert output.count("success=True") == 2
    assert not result["chiller"].is_connected
    assert not endpoint.is_open and not endpoint.applied_writes


def test_timing_notebook_attempts_disconnect_after_failed_connect(
    hardware, run_tutorial, monkeypatch, capsys
):
    from tark_chiller.controller import TarkChiller

    _, endpoint = hardware
    connect = TarkChiller.connect

    def interrupted_connect(self):
        connect(self)
        raise KeyboardInterrupt

    monkeypatch.setattr(TarkChiller, "connect", interrupted_connect)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("04_connection_timing.ipynb")
    output = capsys.readouterr().out
    assert "success=False" in output and "success=True" in output
    assert not endpoint.is_open and not endpoint.applied_writes


@pytest.mark.parametrize("document", ["README.md", "docs/api.md"])
def test_documented_python_blocks_use_serial_driver(document, tmp_path, monkeypatch):
    blocks = re.findall(r"```python\n(.*?)```", (ROOT / document).read_text(encoding="utf-8"), re.S)
    assert blocks
    for index, source in enumerate(blocks):
        directory = tmp_path / str(index)
        directory.mkdir()
        monkeypatch.chdir(directory)
        endpoint = T255PSimulator() if "T255P" in source else CalSimulator()
        monkeypatch.setattr("serial.Serial", endpoint.factory)
        exec(compile(source, document, "exec"), {})
        assert not endpoint.is_open and not endpoint.applied_writes


def test_csv_tutorial_preserves_loop_error_when_stop_also_fails(
    hardware, run_tutorial, monkeypatch
):
    from tark_chiller.controller import TarkChiller

    _, endpoint = hardware
    primary = RuntimeError("Original recording loop failure")

    def failed_sleep(seconds):
        raise primary

    def failed_join(self, timeout_s):
        raise TimeoutError("CSV worker cleanup failed")

    # No worker is started; exercise tutorial ownership without leaving a thread.
    monkeypatch.setattr(TarkChiller, "start_monitoring", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        TarkChiller,
        "snapshot",
        lambda self: {
            "running": True,
            "service_error": "",
            "logging_error": "",
            "failed_samples": 0,
        },
    )
    monkeypatch.setattr("time.sleep", failed_sleep)
    monkeypatch.setattr(TarkChiller, "_stop_and_join_monitor", failed_join)
    with pytest.raises(RuntimeError) as raised:
        run_tutorial("03_record_csv.ipynb")
    assert raised.value is primary
    assert "CSV worker cleanup failed" in primary.__notes__[0]
    assert not endpoint.is_open and not endpoint.applied_writes
