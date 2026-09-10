"""TEST-006/007/009: polling, persistence and headless integration."""

import csv
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from threading import TIMEOUT_MAX, Event

import pytest

from tark_chiller import Chiller, SimulatedDevice
from tark_chiller.csvlog import CsvLogger
from tark_chiller.device import DeviceStatus
from tark_chiller.monitoring import LiveState, Monitor, Sample


@pytest.fixture
def connected():
    chiller = Chiller(SimulatedDevice())
    chiller.connect()
    yield chiller
    chiller.disconnect()


def test_worker_acquires_without_gui_and_has_single_lifecycle(connected):
    state = LiveState()
    monitor = Monitor(connected, state, interval_s=0.02)
    try:
        monitor.start()
        worker = monitor._thread
        monitor.start()
        assert monitor._thread is worker
        deadline = time.monotonic() + 2
        while len(state.snapshot().history) < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(state.snapshot().history) >= 3
        assert state.snapshot().running
    finally:
        monitor.stop()
    count = len(state.snapshot().history)
    assert not state.snapshot().running
    assert not worker.is_alive()
    monitor.stop()
    assert len(state.snapshot().history) == count


def test_poll_failure_is_blank_and_explicit_reconnect_recovers(connected):
    state = LiveState(capacity=2)
    monitor = Monitor(connected, state)
    monitor.poll_once()
    connected.disconnect()
    failed = monitor.poll_once()
    assert failed.temperature_c is None and failed.setpoint_c is None
    assert not failed.status.connected
    assert "ChillerConnectionError" in failed.error
    assert not connected.is_connected
    connected.connect()
    recovered = monitor.poll_once()
    assert recovered.error is None and recovered.status.connected
    assert len(state.snapshot().history) == 2
    assert state.snapshot().history[0] == failed


def test_snapshot_is_immutable_and_detached(connected):
    state = LiveState(capacity=1)
    monitor = Monitor(connected, state)
    sample = monitor.poll_once()
    old = state.snapshot()
    monitor.poll_once()
    assert old.history == (sample,)
    with pytest.raises(FrozenInstanceError):
        old.history[0].temperature_c = 123


@pytest.mark.parametrize(
    "interval", [0, -1, float("nan"), float("inf"), True, "1", TIMEOUT_MAX * 2, 10**400]
)
def test_invalid_poll_intervals(interval, connected):
    with pytest.raises(ValueError):
        Monitor(connected, LiveState(), interval_s=interval)


def test_unsupported_stop_delay_is_rejected_without_stopping_worker(connected):
    state = LiveState()
    monitor = Monitor(connected, state)
    monitor.start()
    try:
        with pytest.raises(ValueError):
            monitor.stop(timeout_s=TIMEOUT_MAX * 2)
        assert state.snapshot().running
    finally:
        monitor.stop()


def test_csv_round_trip_and_failure_rows(tmp_path, connected):
    path = tmp_path / "observations.csv"
    state = LiveState()
    with CsvLogger(path) as logger:
        monitor = Monitor(connected, state, logger=logger)
        first = monitor.poll_once()
        connected.disconnect()
        monitor.poll_once()
        # Reading before close proves flush, not just context-manager cleanup.
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 2
        assert tuple(rows[0]) == CsvLogger.FIELDS
        assert float(rows[0]["temperature_c"]) == first.temperature_c
        assert datetime.fromisoformat(rows[0]["timestamp_utc"]).utcoffset().total_seconds() == 0
        assert rows[0]["backend"] == "simulator"
        assert rows[1]["temperature_c"] == rows[1]["setpoint_c"] == ""
        assert "ChillerConnectionError" in rows[1]["error"]
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        CsvLogger(path)
    assert path.read_bytes() == original


def test_csv_quoting_and_no_placeholder_zero(tmp_path):
    path = tmp_path / "quoted.csv"
    sample = Sample(
        datetime.now(UTC),
        0.1,
        None,
        None,
        DeviceStatus(False, "simulator", 'comma, quote" and\nnewline'),
        "lost",
    )
    with CsvLogger(path) as logger:
        logger.write(sample)
    with path.open(newline="", encoding="utf-8") as stream:
        row = next(csv.DictReader(stream))
    assert row["status_detail"] == sample.status.detail
    assert row["temperature_c"] == ""


@pytest.mark.parametrize("error_type", [OSError, csv.Error])
def test_disk_error_is_sticky_and_does_not_kill_acquisition(connected, error_type):
    class BrokenLogger:
        calls = 0

        def write(self, _sample):
            self.calls += 1
            raise error_type("simulated disk full")

    broken = BrokenLogger()
    state = LiveState()
    monitor = Monitor(connected, state, logger=broken)
    monitor.poll_once()
    monitor.poll_once()
    assert len(state.snapshot().history) == 2
    assert state.snapshot().latest.temperature_c == 20
    assert "disk full" in state.snapshot().logging_error
    assert broken.calls == 1  # Never append to a potentially corrupted CSV again.


def test_unexpected_worker_failure_is_visible(connected, monkeypatch):
    entered = Event()
    state = LiveState()
    monitor = Monitor(connected, state, interval_s=0.02)

    def fail():
        entered.set()
        raise RuntimeError("test programming failure")

    monkeypatch.setattr(connected, "get_temperature", fail)
    monitor.start()
    assert entered.wait(1)
    monitor.stop()
    assert "test programming failure" in state.snapshot().service_error
    assert not state.snapshot().running


def test_successful_restart_clears_old_service_failure(connected):
    state = LiveState()
    state.set_error("Previous monitoring failure")
    monitor = Monitor(connected, state)
    monitor.start()
    monitor.stop()
    assert state.snapshot().service_error is None


def test_thread_start_failure_rolls_back_lifecycle(connected, monkeypatch):
    from threading import Thread

    def fail_start(_thread):
        raise RuntimeError("cannot start new thread")

    state = LiveState()
    monitor = Monitor(connected, state)
    monkeypatch.setattr(Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="cannot start"):
        monitor.start()
    assert not state.snapshot().running
    assert "could not start" in state.snapshot().service_error
    monitor.stop()  # Must not try to join an unstarted thread.


def test_slow_later_read_does_not_refresh_temperature_timestamp(connected, monkeypatch):
    later_read_started = []

    def slow_setpoint():
        later_read_started.append(datetime.now(UTC))
        time.sleep(0.02)
        return 20.0

    monkeypatch.setattr(connected, "get_setpoint", slow_setpoint)
    sample = Monitor(connected, LiveState()).poll_once()
    assert sample.timestamp_utc <= later_read_started[0]


def test_stop_timeout_is_visible_and_worker_can_be_joined(connected, monkeypatch):
    entered, release = Event(), Event()
    state = LiveState()
    monitor = Monitor(connected, state)

    def wait_for_release():
        entered.set()
        release.wait(2)
        return 20.0

    monkeypatch.setattr(connected, "get_temperature", wait_for_release)
    monitor.start()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError):
            monitor.stop(timeout_s=0.01)
        assert "still active" in state.snapshot().service_error
    finally:
        release.set()
        monitor.stop()
    assert not state.snapshot().running
    assert state.snapshot().service_error is None


def test_headless_cli_csv_and_core_dependency_isolation(tmp_path):
    path = tmp_path / "headless.csv"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tark_chiller",
            "--headless",
            "--duration",
            "0.15",
            "--interval",
            "0.02",
            "--csv",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Stopped simulator" in result.stdout
    with path.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) >= 3
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import tark_chiller; "
        "import tark_chiller.monitoring; "
        "assert 'dash' not in sys.modules and 'serial' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code, src], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_recording_counters_survive_history_eviction_and_stop(tmp_path, connected):
    state = LiveState(capacity=2)
    with CsvLogger(tmp_path / "counters.csv") as logger:
        monitor = Monitor(connected, state, logger=logger)
        for _ in range(3):
            monitor.poll_once()
        connected.disconnect()
        monitor.poll_once()
        snapshot = state.snapshot()
        assert snapshot.sample_count == snapshot.logged_samples == logger.rows_written == 4
        assert snapshot.failed_samples == 1
        assert snapshot.logging_enabled
        assert len(snapshot.history) == 2
        assert snapshot.latest.sampled_monotonic is not None


def test_cli_shutdown_cancels_long_recovery_and_closes_logger(tmp_path, monkeypatch):
    from tark_chiller import RecoveryPolicy
    from tark_chiller import __main__ as cli

    device = SimulatedDevice()
    device.inject_fault("get_temperature", "timeout", count=10)
    loggers = []

    def logger_factory(*args, **kwargs):
        logger = CsvLogger(*args, **kwargs)
        loggers.append(logger)
        return logger

    monkeypatch.setattr(cli, "SimulatedDevice", lambda **_kwargs: device)
    monkeypatch.setattr(cli, "RecoveryPolicy", lambda *_args: RecoveryPolicy(3, delay_s=5))
    monkeypatch.setattr(cli, "CsvLogger", logger_factory)
    started = time.monotonic()
    cli.main(
        [
            "--headless",
            "--duration",
            "0.15",
            "--interval",
            "0.02",
            "--csv",
            str(tmp_path / "shutdown.csv"),
        ]
    )
    assert time.monotonic() - started < 2
    assert not device.is_connected
    assert len(loggers) == 1 and not loggers[0].is_open


def test_cli_append_creates_distinct_sessions(tmp_path):
    from tark_chiller.__main__ import main

    path = tmp_path / "sessions.csv"
    args = ["--headless", "--duration", "0.05", "--interval", "0.01", "--csv", str(path)]
    main(args)
    main([*args, "--append-csv"])
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len({row["session_id"] for row in rows}) == 2


@pytest.mark.parametrize("shared_resource", ["chiller", "state"])
def test_only_one_monitor_owns_each_connection_and_live_state(connected, shared_resource):
    class Recorder:
        def write(self, _sample):
            pass

    state = LiveState()
    first = Monitor(connected, state, interval_s=0.02, logger=Recorder())
    other_chiller = Chiller(SimulatedDevice())
    other_chiller.connect()
    try:
        first.start()
        second = Monitor(
            connected if shared_resource == "chiller" else other_chiller,
            LiveState() if shared_resource == "chiller" else state,
        )
        assert state.snapshot().running and state.snapshot().logging_enabled
        with pytest.raises(RuntimeError, match="acquisition owner"):
            second.start()
        with pytest.raises(RuntimeError, match="acquisition owner"):
            second.poll_once()
        second.stop()  # An unused monitor cannot alter its active peer's state.
        assert state.snapshot().running and state.snapshot().logging_enabled
        with pytest.raises(RuntimeError, match="worker runs"):
            first.poll_once()
        first.stop()
        second.start()  # Ownership transfers only after the old work is complete.
        second.stop()
    finally:
        first.stop()
        other_chiller.disconnect()


def test_stop_waits_for_external_poll_and_timeout_retains_ownership(connected, monkeypatch):
    entered, release, stopping = Event(), Event(), Event()
    state = LiveState()
    monitor = Monitor(connected, state)
    competitor = Monitor(connected, LiveState())

    def held_temperature():
        entered.set()
        assert release.wait(2)
        return 20.0

    def stop():
        stopping.set()
        monitor.stop(timeout_s=1)

    monkeypatch.setattr(connected, "get_temperature", held_temperature)
    with ThreadPoolExecutor(max_workers=2) as pool:
        poll = pool.submit(monitor.poll_once)
        try:
            assert entered.wait(1)
            with pytest.raises(TimeoutError, match="poll still active"):
                monitor.stop(timeout_s=0.01)
            assert "still active" in state.snapshot().service_error
            with pytest.raises(RuntimeError, match="acquisition owner"):
                competitor.start()
            stopping_poll = pool.submit(stop)
            assert stopping.wait(1)
            # Wait for the lifecycle lock, not a scheduler-dependent sleep.
            deadline = time.monotonic() + 1
            while not monitor._lifecycle.locked() and time.monotonic() < deadline:
                time.sleep(0.001)
            assert monitor._lifecycle.locked()
            assert not stopping_poll.done()
            with pytest.raises(RuntimeError, match="lifecycle"):
                monitor.poll_once()
        finally:
            release.set()
        poll.result(1)
        stopping_poll.result(1)
    assert state.snapshot().sample_count == 1
    assert state.snapshot().service_error is None
    assert not state.snapshot().running
    assert monitor.poll_once().temperature_c == 20  # Explicit new work is allowed.
    competitor.start()
    competitor.stop()


def test_failed_start_releases_both_ownership_claims(connected, monkeypatch):
    from threading import Thread

    state = LiveState()
    monitor = Monitor(connected, state)
    original_start = Thread.start

    def fail_start(_thread):
        raise RuntimeError("injected thread allocation failure")

    monkeypatch.setattr(Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="allocation failure"):
        monitor.start()
    monkeypatch.setattr(Thread, "start", original_start)
    replacement = Monitor(connected, state)
    replacement.start()
    replacement.stop()


def test_logged_row_and_sample_publish_together(connected, monkeypatch):
    state = LiveState()
    entering_publication, release = Event(), Event()
    append = state.append

    class Recorder:
        def write(self, _sample):
            pass

    def held_publication(sample, **kwargs):
        entering_publication.set()
        assert release.wait(2)
        append(sample, **kwargs)

    monitor = Monitor(connected, state, logger=Recorder())
    monkeypatch.setattr(state, "append", held_publication)
    with ThreadPoolExecutor(max_workers=1) as pool:
        poll = pool.submit(monitor.poll_once)
        try:
            assert entering_publication.wait(1)
            snapshot = state.snapshot()
            assert snapshot.logged_samples == snapshot.sample_count == 0
        finally:
            release.set()
        poll.result(1)
    assert state.snapshot().logged_samples == state.snapshot().sample_count == 1


def test_disconnected_status_diagnostic_is_retained(connected):
    connected.disconnect()
    sample = Monitor(connected, LiveState()).poll_once()
    assert not sample.status.connected and sample.temperature_c is None
    assert sample.status.detail == "intentionally disconnected"
    assert "ChillerConnectionError" in sample.error


def test_replacement_logger_clears_failure_only_after_successful_recording(connected, tmp_path):
    state = LiveState()

    class FailedSink:
        def write(self, _sample):
            raise OSError("synthetic disk full")

    failed = Monitor(connected, state, logger=FailedSink())
    failed.poll_once()
    failed.stop()
    assert "disk full" in state.snapshot().logging_error
    Monitor(connected, state).poll_once()
    assert "disk full" in state.snapshot().logging_error

    with CsvLogger(tmp_path / "replacement.csv") as logger:
        replacement = Monitor(connected, state, logger=logger)
        assert "disk full" in state.snapshot().logging_error
        replacement.poll_once()
        snapshot = state.snapshot()
        assert snapshot.logging_error is None
        assert snapshot.logging_enabled
        assert snapshot.logged_samples == logger.rows_written == 1
        assert snapshot.sample_count == 3
        replacement.stop()
