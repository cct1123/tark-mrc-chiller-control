"""Optional monitoring: lifetime, data integrity, disk faults and shutdown."""

import csv
import math
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from tark_chiller import Chiller, Simulator


def wait_for(condition, timeout=2):
    deadline = monotonic() + timeout
    while not condition():
        assert monotonic() < deadline, "condition did not become true"
        sleep(0.005)


def test_monitor_csv_bounded_history_and_restart(tmp_path):
    path = tmp_path / "run.csv"
    with Chiller(Simulator()) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.01, csv_path=path, history_size=3)
        wait_for(lambda: monitor.snapshot().sample_count >= 5)
        with pytest.raises(RuntimeError, match="already"):
            chiller.start_monitoring(csv_path=tmp_path / "duplicate.csv")
        assert not (tmp_path / "duplicate.csv").exists()
        snap = monitor.snapshot()
        assert len(snap.history) == 3
        assert snap.sample_count == snap.logged_samples >= 5
        with pytest.raises(FrozenInstanceError):
            snap.latest.temperature_c = 0
        chiller.stop_monitoring()
        assert chiller.read_temperature() > 0
        final = monitor.snapshot()
        assert not final.running and not final.logging_enabled
        assert snap.sample_count <= final.sample_count
        new = chiller.start_monitoring(interval_s=0.01)
        assert new is not monitor
        wait_for(lambda: new.snapshot().latest is not None)
    assert not new.running and not chiller.is_connected
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == final.logged_samples
    assert float(rows[0]["temperature_c"]) == 20
    assert rows[0]["timestamp_utc"].endswith("+00:00")
    assert all(r["connected"] == "True" and r["backend"] == "simulator" for r in rows)
    assert [float(r["elapsed_s"]) for r in rows] == sorted(float(r["elapsed_s"]) for r in rows)
    original = path.read_bytes()
    with Chiller(Simulator()) as chiller, pytest.raises(FileExistsError):
        chiller.start_monitoring(csv_path=path)
    assert path.read_bytes() == original
    path.unlink()  # File handles must be closed on Windows.


def test_failure_rows_are_blank_and_recover_on_explicit_connect(tmp_path, monkeypatch):
    backend = Simulator()
    original = backend._read_temperature
    backend._read_temperature = lambda: (_ for _ in ()).throw(TimeoutError("cable lost"))
    path = tmp_path / "fault.csv"
    with Chiller(backend) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: monitor.snapshot().failed_samples >= 2)
        snapshot = monitor.snapshot()
        assert snapshot.latest.temperature_c is None
        assert snapshot.latest.setpoint_c is None
        assert not snapshot.latest.status.connected
        backend._read_temperature = original
        chiller.connect()
        wait_for(lambda: monitor.snapshot().latest.error == "")
    rows = list(csv.DictReader(path.open(newline="")))
    failed = [row for row in rows if row["error"]]
    assert len(failed) >= 2
    assert all(row["temperature_c"] == row["setpoint_c"] == "" for row in failed)


@pytest.mark.parametrize(
    "config",
    [{"interval_s": x} for x in (0, -1, math.nan, math.inf, True)]
    + [{"history_size": x} for x in (0, -1, 1.2, True)],
)
def test_invalid_monitor_settings_do_not_create_files_or_workers(config, tmp_path):
    path = tmp_path / "bad.csv"
    with Chiller(Simulator()) as chiller, pytest.raises(ValueError):
        chiller.start_monitoring(csv_path=path, **config)
    assert not path.exists()


def test_disk_full_stops_recording_but_keeps_sampling(tmp_path, monkeypatch):
    original_open = Path.open
    streams = []

    class FailingFile:
        def __init__(self, stream):
            self.stream = stream
            self.writes = 0

        def write(self, row):
            self.writes += 1
            if self.writes > 2:
                raise OSError("disk full")
            return self.stream.write(row)

        def flush(self):
            self.stream.flush()

        def close(self):
            self.stream.close()

    def wrapped_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        streams.append(stream)
        return FailingFile(stream)

    monkeypatch.setattr(Path, "open", wrapped_open)
    with Chiller(Simulator()) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.01, csv_path=tmp_path / "full.csv")
        wait_for(lambda: monitor.snapshot().sample_count >= 5)
        snapshot = monitor.snapshot()
        assert "disk full" in snapshot.logging_error
        assert snapshot.running and not snapshot.logging_enabled
        assert snapshot.logged_samples == 1
        assert snapshot.failed_samples == 0
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyboardInterrupt(), SystemExit(9)])
def test_unexpected_worker_exit_is_visible_and_csv_closed(error, monkeypatch, tmp_path):
    backend = Simulator()
    monkeypatch.setattr(backend, "_read_temperature", lambda: (_ for _ in ()).throw(error))
    path = tmp_path / "fatal.csv"
    with Chiller(backend) as chiller:
        monitor = chiller.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: not monitor.running)
        assert type(error).__name__ in monitor.snapshot().service_error
        assert not monitor.snapshot().logging_enabled
    path.unlink()


def test_pending_read_stop_timeout_retains_worker_until_join(monkeypatch, tmp_path):
    backend = Simulator()
    entered, release = Event(), Event()

    def slow_read():
        entered.set()
        assert release.wait(3)
        return 20

    monkeypatch.setattr(backend, "_read_temperature", slow_read)
    with Chiller(backend) as chiller:
        monitor = chiller.start_monitoring(csv_path=tmp_path / "pending.csv")
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError, match="still owned"):
                chiller.stop_monitoring(0.01)
            assert monitor.running
            assert monitor.snapshot().logging_enabled
            assert "timed out" in monitor.snapshot().service_error
        finally:
            release.set()
        chiller.stop_monitoring()
        assert not monitor.running
        assert not monitor.snapshot().logging_enabled


def test_thread_start_failure_leaves_new_run_possible(tmp_path, monkeypatch):
    original = Thread.start

    def fail_start(thread):
        raise RuntimeError("cannot start thread")

    with Chiller(Simulator()) as chiller:
        monkeypatch.setattr(Thread, "start", fail_start)
        path = tmp_path / "start.csv"
        with pytest.raises(RuntimeError, match="cannot start"):
            chiller.start_monitoring(csv_path=path)
        path.unlink()
        monkeypatch.setattr(Thread, "start", original)
        monitor = chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: monitor.snapshot().sample_count > 0)


def test_interrupted_start_after_launch_is_joined_without_polling(tmp_path, monkeypatch):
    original = Thread.start

    def interrupted_start(thread):
        original(thread)
        raise KeyboardInterrupt

    with Chiller(Simulator()) as chiller:
        monkeypatch.setattr(Thread, "start", interrupted_start)
        path = tmp_path / "interrupt.csv"
        with pytest.raises(KeyboardInterrupt):
            chiller.start_monitoring(csv_path=path)
        chiller.stop_monitoring()
        assert path.read_text().count("\n") == 1
        path.unlink()


def test_thread_construction_interruption_closes_csv(tmp_path, monkeypatch):
    import tark_chiller.monitor as module

    def interrupted_constructor(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "Thread", interrupted_constructor)
    path = tmp_path / "constructor.csv"
    with Chiller(Simulator()) as chiller, pytest.raises(KeyboardInterrupt):
        chiller.start_monitoring(csv_path=path)
    path.unlink()


def test_start_gate_interruption_closes_csv(tmp_path, monkeypatch):
    import tark_chiller.monitor as module

    real_event, calls = module.Event, 0

    def interrupted_event():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real_event()

    monkeypatch.setattr(module, "Event", interrupted_event)
    path = tmp_path / "gate.csv"
    with Chiller(Simulator()) as chiller, pytest.raises(KeyboardInterrupt):
        chiller.start_monitoring(csv_path=path)
    path.unlink()


def test_slow_setpoint_read_does_not_make_temperature_look_fresh(monkeypatch):
    backend = Simulator()
    entered, release = Event(), Event()

    def slow_setpoint():
        entered.set()
        assert release.wait(2)
        return 20

    monkeypatch.setattr(backend, "_read_setpoint", slow_setpoint)
    with Chiller(backend) as chiller:
        monitor = chiller.start_monitoring(interval_s=10)
        assert entered.wait(1)
        sampled_before = monotonic()
        sleep(0.05)
        release.set()
        wait_for(lambda: monitor.snapshot().latest is not None)
        assert monitor.snapshot().latest.sampled_monotonic <= sampled_before
