"""Researcher scripts exercise the installed API and CSV with no hardware."""

import csv
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

import tark_chiller
from tark_chiller.errors import ProtocolUnavailableError
from tark_chiller.transport import RS485Mode, SerialSettings

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("01_read_temperature.py", "Temperature: 20.00 Celsius"),
        ("02_set_temperature.py", "Reported setpoint: 18.00 Celsius"),
    ],
)
def test_read_and_set_examples(script, expected, tmp_path):
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


@pytest.mark.parametrize(
    ("script", "filename", "minimum_rows"),
    [
        ("03_log_temperature.py", "03_temperature.csv", 5),
        ("04_monitor_experiment.py", "04_experiment.csv", 15),
    ],
)
def test_recording_examples_complete_with_valid_csv(script, filename, minimum_rows, tmp_path):
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "outputs" / filename
    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) >= minimum_rows
    assert len({row["session_id"] for row in rows}) == 1
    assert all(row["backend"] == "simulator" and not row["error"] for row in rows)
    assert all(2 <= float(row["setpoint_c"]) <= 40 for row in rows)
    if script.startswith("04"):
        assert float(rows[-1]["temperature_c"]) < float(rows[0]["temperature_c"])
    else:
        assert len(rows) == 5
    before = output.read_bytes()
    rerun = subprocess.run(
        [sys.executable, str(EXAMPLES / script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert rerun.returncode != 0
    assert output.read_bytes() == before


def test_dashboard_example_uses_the_application_entry_point(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLES / "05_launch_dashboard.py"),
            "--headless",
            "--duration",
            "0.2",
            "--interval",
            "0.02",
            "--csv",
            "session.csv",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Stopped simulator" in result.stdout
    with (tmp_path / "session.csv").open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) >= 3


def test_csv_example_reports_an_isolated_disk_failure(tmp_path, monkeypatch):
    class FullDiskLogger(tark_chiller.CsvLogger):
        def write(self, sample):
            raise OSError("simulated disk full")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tark_chiller, "CsvLogger", FullDiskLogger)
    example = runpy.run_path(str(EXAMPLES / "03_log_temperature.py"))
    with pytest.raises(RuntimeError, match="simulated disk full"):
        example["main"]()
    # Cleanup released the real file lock even though the sample write failed.
    with tark_chiller.CsvLogger("outputs/03_temperature.csv", append=True) as logger:
        assert logger.rows_written == 0


@pytest.mark.parametrize("rs485", [False, True])
def test_hardware_template_refuses_before_serial_creation(rs485, monkeypatch):
    def forbidden_factory(**kwargs):
        pytest.fail("Hardware template attempted to create a serial endpoint")

    monkeypatch.setattr("tark_chiller.transport._serial_factory", forbidden_factory)
    example = runpy.run_path(str(EXAMPLES / "06_hardware_configuration.py"))
    # Synthetic fixture parameters, never recommended settings or an OS port.
    settings = SerialSettings("FAKE-ONLY", 19200, 7, "E", 2, False, False, False, False, False)
    mode = RS485Mode(True, False, False, None, None) if rs485 else None
    chiller = example["configure"](settings, rs485_mode=mode)
    try:
        with pytest.raises(ProtocolUnavailableError, match="communication manual"):
            chiller.connect()
        assert not chiller.is_connected
    finally:
        chiller.disconnect()


def test_hardware_template_reports_missing_protocol_without_traceback(tmp_path):
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "06_hardware_configuration.py")],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Hardware unavailable" in result.stderr and "communication manual" in result.stderr
    assert "Traceback" not in result.stderr
