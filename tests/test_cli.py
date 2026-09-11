"""Release entry points: real signals, clean ownership and missing extras."""

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import tark_chiller
from tark_chiller import Chiller
from tark_chiller import __main__ as cli


@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM", "SIGBREAK"])
def test_continuous_cli_signal_joins_worker_closes_csv_and_disconnects(name, tmp_path, monkeypatch):
    sig = getattr(signal, name, None)
    if sig is None:
        pytest.skip(f"{name} is not available on this platform")
    previous_handler = signal.getsignal(sig)
    monitors = []
    real_start, real_sleep = Chiller.start_monitoring, cli.time.sleep

    def capture_monitor(self, *args, **kwargs):
        monitor = real_start(self, *args, **kwargs)
        monitors.append(monitor)
        return monitor

    waits = 0

    def interrupt_after_samples(seconds):
        nonlocal waits
        waits += 1
        assert waits < 30, "Monitoring failed to produce samples"
        real_sleep(seconds)
        if monitors[0].snapshot().sample_count >= 3:
            signal.raise_signal(sig)

    monkeypatch.setattr(Chiller, "start_monitoring", capture_monitor)
    monkeypatch.setattr(cli.time, "sleep", interrupt_after_samples)
    path = tmp_path / "continuous.csv"
    cli.main(["--headless", "--interval", "0.01", "--csv", str(path)])
    monitor = monitors[0]
    snapshot = monitor.snapshot()
    assert snapshot.sample_count >= 3
    assert not snapshot.running and not snapshot.service_error
    assert not monitor._chiller.is_connected
    assert not snapshot.logging_enabled
    assert signal.getsignal(sig) is previous_handler
    path.unlink()  # Windows refuses this when a writer handle is still open.


@pytest.mark.parametrize("headless", [False, True])
def test_installed_core_without_optional_packages(headless, tmp_path):
    package_root = tmp_path / "bare"
    shutil.copytree(Path(tark_chiller.__file__).resolve().parent, package_root / "tark_chiller")
    args = ["--csv", str(tmp_path / "core.csv")]
    if headless:
        args += ["--headless", "--duration", "0.1"]
    code = (
        f"import sys; sys.path.insert(0, {str(package_root)!r}); "
        f"from tark_chiller.__main__ import main; main({args!r})"
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
        assert "Dashboard unavailable" in result.stderr and '".[gui]"' in result.stderr
        assert "Traceback" not in result.stderr
    assert (tmp_path / "core.csv").exists() is headless


@pytest.mark.parametrize("args", [["--duration", "1"], ["--headless", "--duration", "0"]])
def test_invalid_duration_is_rejected_before_creating_csv(args, tmp_path):
    path = tmp_path / "invalid.csv"
    with pytest.raises(SystemExit) as error:
        cli.main([*args, "--csv", str(path)])
    assert error.value.code == 2
    assert not path.exists()
