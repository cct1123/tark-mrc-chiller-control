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

from tark_chiller.controller import ProtocolError
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
                assert all(alias.name.split(".")[0] not in ("hardware", "examples")
                           for alias in node.names)


def test_examples_have_both_formats_except_script_only_dashboard():
    assert len(TUTORIALS) == 5 and len(SCRIPTS) == 6
    assert {Path(name).stem for name in TUTORIALS} == {
        Path(name).stem for name in SCRIPTS if name != "04_dashboard.py"
    }
    assert not (EXAMPLES / "04_dashboard.ipynb").exists()


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
            notebook_cells(name) if name.endswith(".ipynb")
            else [{"source": [(EXAMPLES / name).read_text(encoding="utf-8")]}]
        )
        for cell in cells:
            tree = ast.parse("".join(cell["source"]))
            for node in tree.body:
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else [node.target] if isinstance(node, ast.AnnAssign) else []
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
    run_tutorial("01_read_status.ipynb")
    output = capsys.readouterr().out
    assert "Temperature:       20.00 C" in output
    assert "Reported setpoint:" in output and "Details:" in output
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
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    run_tutorial("02_set_temperature.ipynb", TARGET_C=19.5)
    assert "Reported setpoint: 19.50 C" in capsys.readouterr().out
    assert endpoint.applied_writes == 1 and not endpoint.is_open


@pytest.mark.parametrize("target", [float("nan"), float("inf"), True, -1, 46])
def test_target_tutorial_keeps_public_value_guards(hardware, run_tutorial, monkeypatch, target):
    _, endpoint = hardware
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(ValueError):
        run_tutorial("02_set_temperature.ipynb", TARGET_C=target)
    assert not endpoint.is_open and not endpoint.applied_writes


def test_target_tutorial_cancellation_sends_no_target(hardware, run_tutorial, monkeypatch):
    _, endpoint = hardware

    def cancel(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("02_set_temperature.ipynb")
    assert not endpoint.is_open and not endpoint.applied_writes


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
    from dash import Dash

    _, endpoint = hardware
    closed = []

    def check_app(app):
        assert app.test_client().get("/").status_code == 200
        layout = app.test_client().get("/_dash-layout").get_data(as_text=True)
        assert "Read-only session" not in layout

    def run(app, **settings):
        assert settings == dict(host="127.0.0.1", port=8056, threaded=True,
                                debug=False, use_reloader=False)
        check_app(app.server)
        assert endpoint.is_open
        response = app.server.test_client().post("/_dash-update-component", json={
                "output": "setpoint-result.children",
                "outputs": {"id": "setpoint-result", "property": "children"},
                "inputs": [{"id": "apply-setpoint", "property": "n_clicks", "value": 1}],
                "state": [{"id": "setpoint-input", "property": "value", "value": 19.0}],
                "changedPropIds": ["apply-setpoint.n_clicks"],
        })
        assert response.status_code == 200 and "completed" in response.get_data(as_text=True)
        closed.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(Dash, "run", run)
    result = run_tutorial("04_dashboard.py", CSV_PATH="outputs/dashboard.csv", INTERVAL_S=0.01)
    assert closed == [True]
    assert not result["chiller"].snapshot()["running"]
    assert not endpoint.is_open and endpoint.applied_writes == 1
    path = tmp_path / "outputs/dashboard.csv"
    assert path.is_file()
    path.unlink()


def test_timing_notebook_connects_and_releases(hardware, run_tutorial, capsys):
    _, endpoint = hardware
    result = run_tutorial("05_connection_timing.ipynb")
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
        run_tutorial("05_connection_timing.ipynb")
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
    monkeypatch.setattr(TarkChiller, "_join", failed_join)
    with pytest.raises(RuntimeError) as raised:
        run_tutorial("03_record_csv.ipynb")
    assert raised.value is primary
    assert "CSV worker cleanup failed" in primary.__notes__[0]
    assert not endpoint.is_open and not endpoint.applied_writes
