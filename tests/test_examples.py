"""Runnable examples and documentation blocks use the production driver."""

import csv
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest

from tark_chiller import ProtocolUnavailableError
from tark_chiller.serial import RS485Mode, SerialSettings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def run_example(script, cwd, *args, timeout=25):
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, str(EXAMPLES / script), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("01_read_temperature.py", "Temperature: 20.00 Celsius"),
        ("02_set_temperature.py", "Reported setpoint: 18.00 Celsius"),
    ],
)
def test_read_and_set_examples(script, expected, tmp_path):
    result = run_example(script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


@pytest.mark.parametrize(
    ("script", "filename", "minimum_rows"),
    [
        ("03_log_temperature.py", "03_temperature.csv", 4),
        ("04_monitor_experiment.py", "04_experiment.csv", 15),
    ],
)
def test_recording_examples_complete_without_overwriting(script, filename, minimum_rows, tmp_path):
    result = run_example(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "outputs" / filename
    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    valid = [row for row in rows if not row["error"]]
    assert len(valid) >= minimum_rows
    assert all(row["backend"] == "simulator" for row in valid)
    assert all(2 <= float(row["setpoint_c"]) <= 40 for row in valid)
    assert all(row["temperature_c"] == "" for row in rows if row["error"])
    if script.startswith("04"):
        assert float(valid[-1]["temperature_c"]) < float(valid[0]["temperature_c"])
    before = output.read_bytes()
    rerun = run_example(script, tmp_path)
    assert rerun.returncode != 0
    assert output.read_bytes() == before


def test_dashboard_example_uses_the_application_entry_point(tmp_path):
    result = run_example(
        "05_launch_dashboard.py",
        tmp_path,
        "--headless",
        "--duration",
        "0.2",
        "--interval",
        "0.02",
        "--csv",
        "session.csv",
    )
    assert result.returncode == 0, result.stderr
    assert "Stopped simulator" in result.stdout
    with (tmp_path / "session.csv").open(newline="") as stream:
        assert len(list(csv.DictReader(stream))) >= 3


def test_csv_example_reports_worker_write_failure_and_closes_file(tmp_path, monkeypatch):
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
                raise OSError("simulated disk full")
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()

        def close(self):
            self.stream.close()

    def open_file(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "03_temperature.csv" and args and args[0] == "x":
            wrapper = FullDisk(stream)
            opened.append(wrapper)
            return wrapper
        return stream

    def wait_for_failure(_seconds):
        assert failed.wait(2), "The production worker never attempted its CSV row"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", open_file)
    example = runpy.run_path(str(EXAMPLES / "03_log_temperature.py"))
    example["main"].__globals__["sleep"] = wait_for_failure
    with pytest.raises(RuntimeError, match="simulated disk full"):
        example["main"]()
    assert opened[0].stream.closed


@pytest.mark.parametrize("rs485", [False, True])
def test_hardware_template_refuses_before_serial_creation(rs485, monkeypatch):
    def forbidden_factory(**kwargs):
        pytest.fail("Hardware template attempted to create a serial endpoint")

    monkeypatch.setattr("tark_chiller.serial._serial_factory", forbidden_factory)
    example = runpy.run_path(str(EXAMPLES / "06_hardware_configuration.py"))
    # Arbitrary software fixture values; never recommended as MRC settings.
    settings = SerialSettings("FAKE-ONLY", 19200, 7, "E", 2, False, False, False, False, False)
    mode = RS485Mode(True, False, False, None, None) if rs485 else None
    chiller = example["configure"](settings, rs485=mode)
    try:
        with pytest.raises(ProtocolUnavailableError, match="communication manual"):
            chiller.connect()
        assert not chiller.is_connected
    finally:
        chiller.disconnect()


def test_hardware_template_reports_dependency_without_traceback(tmp_path):
    result = run_example("06_hardware_configuration.py", tmp_path)
    assert result.returncode == 1
    assert "Hardware unavailable" in result.stderr and "communication manual" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("document", ["README.md", "docs/api.md"])
def test_documented_python_blocks(document, tmp_path):
    blocks = re.findall(r"```python\n(.*?)```", (ROOT / document).read_text(encoding="utf-8"), re.S)
    assert blocks
    for index, source in enumerate(blocks):
        directory = tmp_path / str(index)
        directory.mkdir()
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=directory,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
