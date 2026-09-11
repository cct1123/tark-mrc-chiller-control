"""Exercise human examples through production serial I/O with test-only bytes."""

import csv
import importlib.util
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fakes import FakeCodec, FakeSerial, fake_settings

from tark_chiller import ProtocolError
from tark_chiller.serial import RS485Mode

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture
def lab(monkeypatch):
    spec = importlib.util.spec_from_file_location("lab", EXAMPLES / "lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "lab", module)
    monkeypatch.setitem(sys.modules, "examples.lab", module)
    monkeypatch.syspath_prepend(str(ROOT))
    return module


@pytest.fixture
def hardware(lab, monkeypatch):
    """Replace only documented configuration and the OS endpoint, not the driver."""
    endpoint = FakeSerial()
    monkeypatch.setattr("tark_chiller.serial._serial_factory", endpoint.factory)
    monkeypatch.setattr(lab, "SERIAL_SETTINGS", fake_settings())
    monkeypatch.setattr(lab, "CODEC_CLASS", FakeCodec)
    return endpoint


def wait_for_rows(path, minimum=1):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) >= minimum:
            return rows
        sleep(0.01)
    pytest.fail(f"Recording did not produce {minimum} rows")


@pytest.mark.parametrize("rs485", [False, True])
def test_configured_lab_uses_serial_backend(lab, hardware, monkeypatch, rs485):
    mode = RS485Mode(True, False, False, None, None) if rs485 else None
    monkeypatch.setattr(lab, "RS485_MODE", mode)
    chiller = lab.create_chiller()
    assert not hardware.factory_arguments  # Construction has no I/O.
    with chiller:
        assert chiller.read_status().backend == "hardware"
        assert chiller.read_temperature() == 20
        assert bool(hardware.rs485_mode) == rs485
    assert not hardware.is_open
    assert hardware.applied_writes == 0


@pytest.mark.parametrize("missing", ["SERIAL_SETTINGS", "CODEC_CLASS"])
def test_incomplete_configuration_never_creates_port(lab, hardware, monkeypatch, missing):
    monkeypatch.setattr(lab, missing, None)
    with pytest.raises(ProtocolError, match="communication manual"):
        lab.create_chiller()
    assert not hardware.factory_arguments


def test_separate_example_controllers_do_not_share_protocol_state(lab, hardware, monkeypatch):
    second_endpoint = FakeSerial()
    endpoints = iter([hardware, second_endpoint])
    monkeypatch.setattr(
        "tark_chiller.serial._serial_factory",
        lambda **settings: next(endpoints).factory(**settings),
    )
    second_read_done = Event()
    original_read = hardware.read

    def delayed_read(size=1):
        hardware.read_pending.set()
        assert second_read_done.wait(2)
        return original_read(size)

    monkeypatch.setattr(hardware, "read", delayed_read)
    with lab.create_chiller() as first, lab.create_chiller() as second:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.read_temperature)
            try:
                assert hardware.read_pending.wait(1)
                assert second.read_temperature() == 20
            finally:
                second_read_done.set()
            assert pending.result(timeout=2) == 20
    assert not hardware.is_open and not second_endpoint.is_open


@pytest.mark.parametrize("command", ["read", "set", "log", "monitor", "gui"])
def test_unconfigured_commands_explain_dependency_without_simulator_or_traceback(command, tmp_path):
    args = [command]
    if command == "set":
        args.append("18")
    elif command == "log":
        args.extend(["--csv", "run.csv"])
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "lab.py"), *args],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "Hardware not configured" in result.stderr
    assert "communication manual" in result.stderr
    assert "No port was opened" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_read_example_is_read_only(lab, hardware, capsys):
    lab.main(["read"])
    output = capsys.readouterr().out
    assert "Temperature: 20.00 Celsius" in output
    assert "hardware" in output
    assert hardware.applied_writes == 0
    assert not hardware.is_open


def test_setpoint_example_sends_one_explicit_target(lab, hardware, capsys):
    lab.main(["set", "19.5"])
    output = capsys.readouterr().out
    assert "Original setpoint: 20.00 Celsius" in output
    assert "Reported setpoint: 19.50 Celsius" in output
    assert hardware.applied_writes == 1
    assert not hardware.is_open


@pytest.mark.parametrize("target", ["nan", "inf", "1", "41", "68"])
def test_setpoint_example_uses_public_safety_check(lab, hardware, target):
    with pytest.raises(ValueError):
        lab.main(["set", target])
    assert hardware.applied_writes == 0
    assert not hardware.is_open


def test_setpoint_example_requires_operator_input(lab, hardware):
    with pytest.raises(SystemExit) as error:
        lab.main(["set"])
    assert error.value.code == 2
    assert hardware.factory_arguments == []


@pytest.mark.parametrize("args", [["log"], ["read", "20"], ["set", "20", "--csv", "run.csv"]])
def test_invalid_lab_commands_do_not_open_hardware(lab, hardware, args):
    with pytest.raises(SystemExit) as error:
        lab.main(args)
    assert error.value.code == 2
    assert hardware.factory_arguments == []


def test_setpoint_example_reports_different_readback_without_retry(lab, hardware, capsys):
    original_write = hardware.write

    def write(request):
        result = original_write(request)
        if b"set_setpoint" in request:
            hardware.setpoint = 19.0
        return result

    hardware.write = write
    with pytest.raises(RuntimeError, match="Target not confirmed"):
        lab.main(["set", "19.5"])
    assert "Readback matches" not in capsys.readouterr().out
    assert hardware.applied_writes == 1
    assert not hardware.is_open


def test_setpoint_example_does_not_replay_uncertain_write(lab, hardware):
    # Permit the initial setpoint read, then corrupt the write acknowledgement.
    original_write = hardware.write

    def write(request):
        hardware.response = b"bad\n" if b"set_setpoint" in request else None
        return original_write(request)

    hardware.write = write
    with pytest.raises(ProtocolError, match="response identity"):
        lab.main(["set", "19.5"])
    assert hardware.applied_writes == 1
    assert not hardware.is_open


def test_csv_example_records_physical_path_and_refuses_overwrite(
    lab, hardware, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    main = lab.main
    main(["log", "--csv", "outputs/temperature.csv"])
    path = tmp_path / "outputs" / "temperature.csv"
    rows = wait_for_rows(path, 4)
    assert all(row["backend"] == "hardware" and not row["error"] for row in rows)
    assert "saved rows:" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        main(["log", "--csv", "outputs/temperature.csv"])
    assert path.read_bytes() == before
    assert hardware.applied_writes == 0
    assert not hardware.is_open


@pytest.mark.parametrize("record", [False, True])
def test_continuous_example_stops_on_ctrl_c(lab, hardware, tmp_path, monkeypatch, record):
    monkeypatch.chdir(tmp_path)
    main = lab.main
    path = tmp_path / "outputs" / "experiment.csv"

    def interrupt(_seconds):
        if record:
            wait_for_rows(path, 2)
        else:
            assert hardware.read_pending.wait(2)
        raise KeyboardInterrupt

    monkeypatch.setitem(main.__globals__, "sleep", interrupt)
    main(["monitor", "--csv", "outputs/experiment.csv"] if record else ["monitor"])
    assert hardware.applied_writes == 0
    assert not hardware.is_open
    if record:
        assert all(row["backend"] == "hardware" for row in wait_for_rows(path, 2))
        path.unlink()  # Windows permits this only after the file is closed.
    else:
        assert list(tmp_path.iterdir()) == []


def test_dashboard_example_uses_real_app_and_owns_shutdown(lab, hardware, tmp_path, monkeypatch):
    from dash import Dash

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "outputs" / "dashboard.csv"

    def run(app, **kwargs):
        assert kwargs == dict(host="127.0.0.1", port=8050, debug=False, use_reloader=False)
        wait_for_rows(path)
        client = app.server.test_client()
        assert client.get("/").status_code == 200
        layout = client.get("/_dash-layout")
        assert layout.status_code == 200
        assert "hardware" in layout.get_data(as_text=True)
        raise KeyboardInterrupt

    monkeypatch.setattr(Dash, "run", run)
    lab.main(["gui", "--csv", "outputs/dashboard.csv"])
    assert not hardware.is_open
    assert hardware.applied_writes == 0
    path.unlink()


def test_csv_example_reports_disk_failure(lab, hardware, tmp_path, monkeypatch):
    original_open = Path.open
    failed = Event()
    opened = []

    class FullDisk:
        def __init__(self, stream):
            self.stream = stream
            self.writes = 0

        def write(self, value):
            self.writes += 1
            if self.writes > 1:
                failed.set()
                raise OSError("test disk full")
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()

        def close(self):
            self.stream.close()

    def open_file(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "temperature.csv" and args and args[0] == "x":
            opened.append(stream)
            return FullDisk(stream)
        return stream

    def wait_for_failure(_seconds):
        assert failed.wait(2), "The worker never attempted its CSV row"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", open_file)
    main = lab.main
    monkeypatch.setitem(main.__globals__, "sleep", wait_for_failure)
    with pytest.raises(RuntimeError, match="test disk full"):
        main(["log", "--csv", "outputs/temperature.csv"])
    assert opened[0].closed
    assert not hardware.is_open


@pytest.mark.parametrize("document", ["README.md", "docs/api.md"])
def test_documented_python_blocks_use_serial_driver(lab, hardware, document, tmp_path, monkeypatch):
    blocks = re.findall(r"```python\n(.*?)```", (ROOT / document).read_text(encoding="utf-8"), re.S)
    assert blocks
    for index, source in enumerate(blocks):
        directory = tmp_path / str(index)
        directory.mkdir()
        monkeypatch.chdir(directory)
        exec(compile(source, document, "exec"), {})
        assert not hardware.is_open
    assert hardware.open_count > 0
