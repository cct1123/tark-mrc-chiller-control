"""Release entry points: real signals, clean ownership and missing extras."""

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import tark_chiller.controller as controller
from tark_chiller import simulator as cli
from tark_chiller.controller import TarkChiller


@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM", "SIGBREAK"])
@pytest.mark.parametrize("model", [[], ["--cal"], ["--t255p"]])
def test_continuous_cli_signal_joins_worker_closes_csv_and_disconnects(
    name, model, tmp_path, monkeypatch
):
    sig = getattr(signal, name, None)
    if sig is None:
        pytest.skip(f"{name} is not available on this platform")
    previous_handler = signal.getsignal(sig)
    controllers = []
    real_start, real_sleep = TarkChiller.start_monitoring, cli.time.sleep

    def capture_controller(self, *args, **kwargs):
        real_start(self, *args, **kwargs)
        controllers.append(self)

    waits = 0

    def interrupt_after_samples(seconds):
        nonlocal waits
        waits += 1
        assert waits < 30, "Monitoring failed to produce samples"
        real_sleep(seconds)
        if controllers[0].snapshot()["sample_count"] >= 3:
            signal.raise_signal(sig)

    monkeypatch.setattr(TarkChiller, "start_monitoring", capture_controller)
    monkeypatch.setattr(cli.time, "sleep", interrupt_after_samples)
    path = tmp_path / "continuous.csv"
    cli.main([*model, "--headless", "--interval", "0.01", "--csv", str(path)])
    chiller = controllers[0]
    snapshot = chiller.snapshot()
    assert snapshot["sample_count"] >= 3
    assert not snapshot["running"] and (not snapshot["service_error"])
    assert not chiller.is_connected
    assert not snapshot["logging_enabled"]
    assert signal.getsignal(sig) is previous_handler
    path.unlink()  # Windows refuses this when a writer handle is still open.


def test_protocol_simulator_selection_is_exclusive_and_refuses_before_io(tmp_path):
    with pytest.raises(SystemExit) as error:
        cli.main(["--cal", "--t255p", "--headless", "--csv", str(tmp_path / "unused.csv")])
    assert error.value.code == 2 and not list(tmp_path.iterdir())


@pytest.mark.parametrize("headless", [False, True])
def test_installed_core_without_optional_packages(headless, tmp_path):
    package_root = tmp_path / "bare"
    shutil.copytree(Path(controller.__file__).resolve().parent, package_root / "tark_chiller")
    args = ["--csv", str(tmp_path / "core.csv")]
    if headless:
        args += ["--headless", "--duration", "0.1"]
    code = (
        f"import sys; sys.path.insert(0, {str(package_root)!r}); "
        f"from tark_chiller.simulator import main; main({args!r})"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if headless:
        assert result.returncode == 0, result.stderr
        assert "Stopped simulator" in result.stdout
    else:
        assert result.returncode == 2
        assert "Dashboard unavailable" in result.stderr and "gui extra" in result.stderr
        assert "Traceback" not in result.stderr
    assert (tmp_path / "core.csv").exists() is headless


@pytest.mark.parametrize("args", [["--duration", "1"], ["--headless", "--duration", "0"]])
def test_invalid_duration_is_rejected_before_creating_csv(args, tmp_path):
    path = tmp_path / "invalid.csv"
    with pytest.raises(SystemExit) as error:
        cli.main([*args, "--csv", str(path)])
    assert error.value.code == 2
    assert not path.exists()


def test_headless_csv_failure_exits_promptly_and_releases_resources(tmp_path, monkeypatch):
    original_open, original_start = Path.open, TarkChiller.start_monitoring
    original_sleep = cli.time.sleep
    streams, controllers = [], []

    class FullDisk:
        def __init__(self, stream):
            self.stream = stream
            self.writes = 0

        def write(self, value):
            self.writes += 1
            if self.writes > 1:
                raise OSError("simulated disk full")
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()

        def close(self):
            self.stream.close()

    def open_csv(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path.name == "full.csv" and args and args[0] == "x":
            streams.append(stream)
            return FullDisk(stream)
        return stream

    def start_monitoring(self, *args, **kwargs):
        original_start(self, *args, **kwargs)
        controllers.append(self)

    waits = 0

    def bounded_sleep(seconds):
        nonlocal waits
        waits += 1
        assert waits < 10, "Headless CLI kept running after CSV recording failed"
        original_sleep(seconds)

    monkeypatch.setattr(Path, "open", open_csv)
    monkeypatch.setattr(TarkChiller, "start_monitoring", start_monitoring)
    monkeypatch.setattr(cli.time, "sleep", bounded_sleep)
    path = tmp_path / "full.csv"
    with pytest.raises(SystemExit, match="simulated disk full"):
        cli.main(["--headless", "--duration", "60", "--interval", "0.01", "--csv", str(path)])
    snapshot = controllers[0].snapshot()
    assert snapshot["logging_error"] and snapshot["logged_samples"] == 0
    assert not snapshot["running"] and (not snapshot["logging_enabled"])
    assert not controllers[0].is_connected
    assert streams[0].closed
    path.unlink()


@pytest.mark.parametrize("model", [[], ["--cal"], ["--t255p"]])
def test_simulator_module_entry_point(model, tmp_path):
    path = tmp_path / "module.csv"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tark_chiller.simulator",
            *model,
            "--headless",
            "--duration",
            "0.1",
            "--interval",
            "0.01",
            "--csv",
            str(path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert "Stopped simulator" in result.stdout
    assert len(path.read_text(encoding="utf-8").splitlines()) >= 2
    path.unlink()


@pytest.mark.parametrize("model", ["CalSimulator", "T255PSimulator"])
def test_cli_preserves_operation_error_and_reports_disconnect_failure(model, monkeypatch):
    endpoint = getattr(cli, model)()
    chiller = endpoint.chiller()
    monkeypatch.setattr(getattr(cli, model), "chiller", lambda self: chiller)
    close = endpoint.close
    primary = RuntimeError("Original application failure")

    def broken_start(**kwargs):
        raise primary

    def broken_close():
        raise OSError("Serial close failed")

    monkeypatch.setattr(chiller, "start_monitoring", broken_start)
    monkeypatch.setattr(endpoint, "close", broken_close)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        with pytest.raises(RuntimeError) as raised:
            cli.main(["--cal" if model == "CalSimulator" else "--t255p", "--headless"])
        assert raised.value is primary
        assert "Serial close failed" in primary.__notes__[0]
        assert signal.getsignal(signal.SIGTERM) is previous
        assert chiller._serial is endpoint
    finally:
        monkeypatch.setattr(endpoint, "close", close)
        chiller.disconnect()


@pytest.mark.parametrize("model", ["CalSimulator", "T255PSimulator"])
def test_cli_exits_unsuccessfully_on_failed_poll(model, monkeypatch):
    endpoint = getattr(cli, model)()
    chiller = endpoint.chiller()
    monkeypatch.setattr(getattr(cli, model), "chiller", lambda self: chiller)

    def failed_read():
        raise OSError("Simulated poll failure")

    monkeypatch.setattr(chiller, "read_temperature", failed_read)
    with pytest.raises(SystemExit, match="simulator poll failed"):
        cli.main(["--cal" if model == "CalSimulator" else "--t255p", "--headless"])
    assert chiller.snapshot()["failed_samples"] > 0
    assert not chiller.running and not endpoint.is_open
