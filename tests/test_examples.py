"""Hardware tutorials exercised through production protocols and memory endpoints."""

import ast
import csv
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tark_chiller import ProtocolError
from tark_chiller.serial import CalSimulator, T255PSimulator

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TUTORIALS = sorted(path.name for path in EXAMPLES.glob("[0-9][0-9]_*.py"))


@pytest.fixture
def configuration(monkeypatch):
    spec = importlib.util.spec_from_file_location("hardware", EXAMPLES / "hardware.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "hardware", module)
    return module


@pytest.fixture(params=["cal", "t255p"])
def hardware(configuration, monkeypatch, request):
    endpoint = CalSimulator() if request.param == "cal" else T255PSimulator()
    monkeypatch.setattr(configuration, "DEVICE_MODEL", request.param)
    monkeypatch.setattr(configuration, "SERIAL_SETTINGS", endpoint.device().settings)
    if request.param == "cal":
        monkeypatch.setattr(configuration, "CAL_ADDRESS", 1)
        monkeypatch.setattr(configuration, "EXPECTED_MODEL", 1)
        monkeypatch.setattr(configuration, "EXPECTED_FIRMWARE", 2)
    monkeypatch.setattr("tark_chiller.serial._serial_factory", endpoint.factory)
    return configuration, endpoint


@pytest.fixture
def run_tutorial(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def run(name, **constants):
        # Edit only tutorial constants to shorten runs and choose test CSV names.
        # Imports, serial settings, codecs, controller and cleanup run unchanged.
        tree = ast.parse((EXAMPLES / name).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in constants:
                        node.value = ast.Constant(constants[target.id])
        namespace = {"__name__": "__main__", "__file__": str(EXAMPLES / name)}
        exec(compile(ast.fix_missing_locations(tree), name, "exec"), namespace)
        return namespace

    return run


def rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_setup_import_and_construction_do_not_open_hardware(hardware):
    configuration, endpoint = hardware
    first, second = configuration.create_chiller(), configuration.create_chiller()
    assert not endpoint.is_open
    assert not first.is_connected and not second.is_connected
    assert first._backend is not second._backend
    assert first._backend._codec is not second._backend._codec
    with first:
        assert first.read_temperature() == 20
        with pytest.raises(ProtocolError, match="writes disabled"):
            first.set_setpoint(20)
    assert not endpoint.is_open and not endpoint.applied_writes


@pytest.mark.parametrize("missing", ["DEVICE_MODEL", "SERIAL_SETTINGS"])
def test_missing_configuration_refuses_before_endpoint_creation(
    configuration, monkeypatch, missing
):
    monkeypatch.setattr(configuration, "DEVICE_MODEL", "t255p")
    monkeypatch.setattr(configuration, "SERIAL_SETTINGS", T255PSimulator().device().settings)
    monkeypatch.setattr(configuration, missing, None)
    monkeypatch.setattr(
        "tark_chiller.serial._serial_factory",
        lambda **kwargs: pytest.fail("Incomplete configuration reached OS endpoint factory"),
    )
    with pytest.raises(ProtocolError, match="No port was opened"):
        configuration.create_chiller()


def test_cal_needs_address_and_t255p_refuses_leftover_cal_fields(configuration, monkeypatch):
    monkeypatch.setattr(configuration, "SERIAL_SETTINGS", CalSimulator().device().settings)
    monkeypatch.setattr(configuration, "DEVICE_MODEL", "cal")
    with pytest.raises(ProtocolError, match="CAL_ADDRESS"):
        configuration.create_chiller()
    monkeypatch.setattr(configuration, "DEVICE_MODEL", "t255p")
    monkeypatch.setattr(configuration, "CAL_ADDRESS", 1)
    with pytest.raises(ProtocolError, match="clear them"):
        configuration.create_chiller()


@pytest.mark.parametrize("name", TUTORIALS)
def test_unconfigured_tutorials_never_open_a_port_or_write_data(name, tmp_path):
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Hardware not configured" in result.stderr
    assert "No port was opened" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_read_tutorial_uses_real_codec_and_closes(hardware, run_tutorial, capsys):
    _, endpoint = hardware
    run_tutorial("01_read_status.py")
    output = capsys.readouterr().out
    assert "Temperature:       20.00 C" in output
    assert "Reported setpoint:" in output and "Details:" in output
    assert not endpoint.is_open and not endpoint.applied_writes


def test_read_tutorial_preserves_read_error_when_port_close_also_fails(
    hardware, run_tutorial, monkeypatch
):
    configuration, endpoint = hardware
    write, close = endpoint.write, endpoint.close
    primary = ProtocolError("Invalid temperature reply")

    def failed_read(request):
        temperature = (
            request[1:2] == b"I"
            if configuration.DEVICE_MODEL == "t255p"
            else (request[1] == 3 and request[2:4] == b"\x00\x1c")
        )
        if temperature:
            raise primary
        return write(request)

    def failed_close():
        raise OSError("Port close failed")

    monkeypatch.setattr(endpoint, "write", failed_read)
    monkeypatch.setattr(endpoint, "close", failed_close)
    try:
        with pytest.raises(ProtocolError) as raised:
            run_tutorial("01_read_status.py")
        assert raised.value is primary
        assert any("Disconnect cleanup failed" in note for note in primary.__notes__)
    finally:
        monkeypatch.setattr(endpoint, "close", close)
        endpoint.close()


def test_target_tutorial_applies_one_request(hardware, run_tutorial, monkeypatch, capsys):
    _, endpoint = hardware
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    run_tutorial("02_set_temperature.py", TARGET_C=19.5)
    assert "Reported setpoint: 19.50 C" in capsys.readouterr().out
    assert endpoint.applied_writes == 1 and not endpoint.is_open


@pytest.mark.parametrize("target", [float("nan"), float("inf"), True, -1, 46])
def test_target_tutorial_keeps_public_value_guards(hardware, run_tutorial, monkeypatch, target):
    _, endpoint = hardware
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(ValueError):
        run_tutorial("02_set_temperature.py", TARGET_C=target)
    assert not endpoint.is_open and not endpoint.applied_writes


def test_target_tutorial_cancellation_sends_no_target(hardware, run_tutorial, monkeypatch):
    _, endpoint = hardware

    def cancel(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("02_set_temperature.py")
    assert not endpoint.is_open and not endpoint.applied_writes


def test_uncertain_target_is_never_replayed(hardware, run_tutorial, monkeypatch):
    configuration, endpoint = hardware
    write = endpoint.write
    commits = []

    def lost_ack(request):
        count = write(request)
        commit = (
            request[1:2] == b"M"
            if configuration.DEVICE_MODEL == "t255p"
            else (request[1] == 6 and request[2:4] == b"\x16\x00")
        )
        if commit:
            commits.append(request)
            raise TimeoutError("Lost write acknowledgement")
        return count

    monkeypatch.setattr(endpoint, "write", lost_ack)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(ProtocolError, match="uncertain"):
        run_tutorial("02_set_temperature.py", TARGET_C=19.5)
    assert len(commits) == endpoint.applied_writes == 1
    assert not endpoint.is_open


def test_csv_tutorial_records_and_refuses_existing_file(hardware, run_tutorial, tmp_path, capsys):
    _, endpoint = hardware
    constants = dict(DURATION_S=0.1, INTERVAL_S=0.01, stamp="test")
    run_tutorial("03_record_csv.py", **constants)
    path = tmp_path / "outputs/hardware_test.csv"
    samples = rows(path)
    assert samples and all(row["temperature_c"] and not row["error"] for row in samples)
    assert "saved rows:" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        run_tutorial("03_record_csv.py", **constants)
    assert path.read_bytes() == before
    assert not endpoint.is_open and not endpoint.applied_writes
    path.unlink()  # Closed writer handle, including on Windows.


def test_csv_tutorial_reports_a_poll_failure(hardware, run_tutorial, monkeypatch):
    configuration, endpoint = hardware
    write = endpoint.write

    def disconnected_during_temperature_read(request):
        temperature = (
            request[1:2] == b"I"
            if configuration.DEVICE_MODEL == "t255p"
            else (request[1] == 3 and request[2:4] == b"\x00\x1c")
        )
        if temperature:
            raise OSError("Test endpoint disconnected")
        return write(request)

    monkeypatch.setattr(endpoint, "write", disconnected_during_temperature_read)
    with pytest.raises(RuntimeError, match="poll failed"):
        run_tutorial("03_record_csv.py", DURATION_S=0.1, INTERVAL_S=0.01)
    assert not endpoint.is_open and not endpoint.applied_writes


def test_csv_tutorial_ctrl_c_closes_worker_file_and_port(
    hardware, run_tutorial, monkeypatch, tmp_path
):
    _, endpoint = hardware

    def cancel(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", cancel)
    with pytest.raises(KeyboardInterrupt):
        run_tutorial("03_record_csv.py", stamp="cancelled")
    assert not endpoint.is_open and not endpoint.applied_writes
    (tmp_path / "outputs/hardware_cancelled.csv").unlink()


def test_dashboard_tutorial_is_read_only_and_joins_on_ctrl_c(
    hardware, run_tutorial, monkeypatch, tmp_path
):
    from dash import Dash

    _, endpoint = hardware

    def run(app, **settings):
        assert settings == dict(host="127.0.0.1", port=8050, debug=False, use_reloader=False)
        assert app.server.test_client().get("/").status_code == 200
        layout = app.server.test_client().get("/_dash-layout").get_data(as_text=True)
        assert "Read-only session" in layout
        raise KeyboardInterrupt

    monkeypatch.setattr(Dash, "run", run)
    result = run_tutorial("04_dashboard.py", CSV_PATH="outputs/dashboard.csv", INTERVAL_S=0.01)
    assert not result["monitor"].snapshot().running
    assert not endpoint.is_open and not endpoint.applied_writes
    path = tmp_path / "outputs/dashboard.csv"
    assert path.is_file()
    path.unlink()


@pytest.mark.parametrize("document", ["README.md", "docs/api.md"])
def test_documented_python_blocks_use_serial_driver(document, tmp_path, monkeypatch):
    blocks = re.findall(r"```python\n(.*?)```", (ROOT / document).read_text(encoding="utf-8"), re.S)
    assert blocks
    for index, source in enumerate(blocks):
        directory = tmp_path / str(index)
        directory.mkdir()
        monkeypatch.chdir(directory)
        endpoint = T255PSimulator() if "T255P" in source else CalSimulator()
        monkeypatch.setattr("tark_chiller.serial._serial_factory", endpoint.factory)
        exec(compile(source, document, "exec"), {})
        assert not endpoint.is_open and not endpoint.applied_writes
