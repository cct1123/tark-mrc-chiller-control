"""Optional monitoring: lifetime, data integrity, disk faults and shutdown."""

import csv
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest
from fakes import FakeChiller


def wait_for(condition, timeout=2):
    deadline = monotonic() + timeout
    while not condition():
        assert monotonic() < deadline, "condition did not become true"
        sleep(0.005)


def test_monitor_csv_bounded_history_and_restart(tmp_path):
    path = tmp_path / "run.csv"
    with FakeChiller() as chiller:
        chiller.start_monitoring(interval_s=0.01, csv_path=path, history_size=3)
        wait_for(lambda: chiller.snapshot()["sample_count"] >= 5)
        with pytest.raises(RuntimeError, match="already"):
            chiller.start_monitoring(csv_path=tmp_path / "duplicate.csv")
        assert not (tmp_path / "duplicate.csv").exists()
        snap = chiller.snapshot()
        assert len(snap["history"]) == 3
        assert snap["sample_count"] == snap["logged_samples"] >= 5
        snap["history"][-1]["temperature_c"] = -999
        assert chiller.snapshot()["history"][-1]["temperature_c"] != -999
        chiller.stop_monitoring()
        assert chiller.read_temperature() > 0
        final = chiller.snapshot()
        assert not final["running"] and (not final["logging_enabled"])
        assert snap["sample_count"] <= final["sample_count"]
        chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: bool(chiller.snapshot()["history"]))
        assert chiller.snapshot()["logged_samples"] == 0
        assert final["logged_samples"] >= 5 and (not final["running"])
    assert not chiller.running and not chiller.is_connected
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == final["logged_samples"]
    assert float(rows[0]["temperature_c"]) == 20
    assert rows[0]["timestamp_utc"].endswith("+00:00")
    assert all(r["connected"] == "True" and r["backend"] == "simulator" for r in rows)
    assert [float(r["elapsed_s"]) for r in rows] == sorted(float(r["elapsed_s"]) for r in rows)
    original = path.read_bytes()
    with FakeChiller() as chiller, pytest.raises(FileExistsError):
        chiller.start_monitoring(csv_path=path)
    assert path.read_bytes() == original
    path.unlink()  # File handles must be closed on Windows.


@pytest.mark.parametrize("failed_read", ["temperature", "setpoint"])
def test_failure_rows_are_blank_and_recover_on_explicit_connect(tmp_path, monkeypatch, failed_read):
    backend = FakeChiller()
    read_method = f"_read_{failed_read}"
    original = getattr(backend, read_method)
    monkeypatch.setattr(
        backend, read_method, lambda: (_ for _ in ()).throw(TimeoutError("cable lost"))
    )
    path = tmp_path / "fault.csv"
    with backend as chiller:
        chiller.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: chiller.snapshot()["failed_samples"] >= 2)
        snapshot = chiller.snapshot()
        assert snapshot["history"][-1]["temperature_c"] is None
        assert snapshot["history"][-1]["setpoint_c"] is None
        assert not snapshot["history"][-1]["status"]["connected"]
        monkeypatch.setattr(backend, read_method, original)
        chiller.connect()
        wait_for(lambda: chiller.snapshot()["history"][-1]["error"] == "")
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
    with FakeChiller() as chiller, pytest.raises(ValueError):
        chiller.start_monitoring(csv_path=path, **config)
    assert not path.exists()


def test_running_monitor_interval_is_read_only_and_worker_stays_paced(monkeypatch):
    reading, release, pacing = Event(), Event(), Event()
    backend = FakeChiller()
    original_read = backend._read_temperature

    def paused_read():
        reading.set()
        assert release.wait(2)
        return original_read()

    monkeypatch.setattr(backend, "_read_temperature", paused_read)
    with backend as chiller:
        chiller.start_monitoring(interval_s=10)
        waits = []
        original_wait = chiller._stop.wait

        def observed_wait(timeout=None):
            waits.append(timeout)
            pacing.set()
            return original_wait(timeout)

        try:
            assert reading.wait(1)
            monkeypatch.setattr(chiller._stop, "wait", observed_wait)
            for value in (math.nan, math.inf, 0, -1, "1", 0.001):
                with pytest.raises(AttributeError):
                    chiller.interval_s = value
            assert chiller.interval_s == 10
        finally:
            release.set()

        assert pacing.wait(1)
        chiller.stop_monitoring()
        snapshot = chiller.snapshot()
        assert len(waits) == 1 and 0 < waits[0] <= 10
        assert snapshot["sample_count"] == 1 and snapshot["failed_samples"] == 0
        assert not snapshot["running"]


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
    with FakeChiller() as chiller:
        chiller.start_monitoring(interval_s=0.01, csv_path=tmp_path / "full.csv")
        wait_for(lambda: chiller.snapshot()["sample_count"] >= 5)
        snapshot = chiller.snapshot()
        assert "disk full" in snapshot["logging_error"]
        assert snapshot["running"] and (not snapshot["logging_enabled"])
        assert snapshot["logged_samples"] == 1
        assert snapshot["failed_samples"] == 0
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyboardInterrupt(), SystemExit(9)])
def test_unexpected_worker_exit_is_visible_and_csv_closed(error, monkeypatch, tmp_path):
    backend = FakeChiller()
    monkeypatch.setattr(backend, "_read_temperature", lambda: (_ for _ in ()).throw(error))
    path = tmp_path / "fatal.csv"
    with backend as chiller:
        chiller.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: not chiller.running)
        assert type(error).__name__ in chiller.snapshot()["service_error"]
        assert not chiller.snapshot()["logging_enabled"]
    path.unlink()


def test_pending_read_stop_timeout_retains_worker_until_join(monkeypatch, tmp_path):
    backend = FakeChiller()
    entered, release = Event(), Event()

    def slow_read():
        entered.set()
        assert release.wait(3)
        return 20

    monkeypatch.setattr(backend, "_read_temperature", slow_read)
    with backend as chiller:
        chiller.start_monitoring(csv_path=tmp_path / "pending.csv")
        assert entered.wait(1)
        try:
            with pytest.raises(TimeoutError, match="still owned"):
                chiller.stop_monitoring(0.01)
            assert chiller.running
            assert chiller.snapshot()["logging_enabled"]
            assert "timed out" in chiller.snapshot()["service_error"]
        finally:
            release.set()
        chiller.stop_monitoring()
        assert not chiller.running
        assert not chiller.snapshot()["logging_enabled"]


def test_thread_start_failure_leaves_new_run_possible(tmp_path, monkeypatch):
    original = Thread.start

    def fail_start(thread):
        raise RuntimeError("cannot start thread")

    with FakeChiller() as chiller:
        monkeypatch.setattr(Thread, "start", fail_start)
        path = tmp_path / "start.csv"
        with pytest.raises(RuntimeError, match="cannot start"):
            chiller.start_monitoring(csv_path=path)
        path.unlink()
        monkeypatch.setattr(Thread, "start", original)
        chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)


def test_interrupted_start_after_launch_is_joined_without_polling(tmp_path, monkeypatch):
    original = Thread.start

    def interrupted_start(thread):
        original(thread)
        raise KeyboardInterrupt

    with FakeChiller() as chiller:
        monkeypatch.setattr(Thread, "start", interrupted_start)
        path = tmp_path / "interrupt.csv"
        with pytest.raises(KeyboardInterrupt):
            chiller.start_monitoring(csv_path=path)
        chiller.stop_monitoring()
        assert path.read_text().count("\n") == 1
        path.unlink()


def test_thread_construction_interruption_closes_csv(tmp_path, monkeypatch):
    import tark_chiller.controller as module

    def interrupted_constructor(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "Thread", interrupted_constructor)
    path = tmp_path / "constructor.csv"
    with FakeChiller() as chiller, pytest.raises(KeyboardInterrupt):
        chiller.start_monitoring(csv_path=path)
    path.unlink()


def test_start_gate_interruption_closes_csv(tmp_path, monkeypatch):
    import tark_chiller.controller as module

    real_event, calls = module.Event, 0

    def interrupted_event():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real_event()

    path = tmp_path / "gate.csv"
    with FakeChiller() as chiller:
        monkeypatch.setattr(module, "Event", interrupted_event)
        with pytest.raises(KeyboardInterrupt):
            chiller.start_monitoring(csv_path=path)
    path.unlink()


def test_slow_setpoint_read_does_not_make_temperature_look_fresh(monkeypatch):
    backend = FakeChiller()
    entered, release = Event(), Event()

    def slow_setpoint():
        entered.set()
        assert release.wait(2)
        return 20

    monkeypatch.setattr(backend, "_read_setpoint", slow_setpoint)
    with backend as chiller:
        chiller.start_monitoring(interval_s=10)
        assert entered.wait(1)
        sampled_before = monotonic()
        sleep(0.05)
        release.set()
        wait_for(lambda: bool(chiller.snapshot()["history"]))
        assert chiller.snapshot()["history"][-1]["sampled_monotonic"] <= sampled_before


def test_blocked_csv_close_does_not_block_snapshots_or_stop_timeout(tmp_path, monkeypatch):
    closing, release = Event(), Event()
    original_open = Path.open
    path = tmp_path / "slow-close.csv"

    class SlowClose:
        def __init__(self, stream):
            self.stream = stream

        def write(self, row):
            return self.stream.write(row)

        def flush(self):
            self.stream.flush()

        def close(self):
            closing.set()
            assert release.wait(3)
            self.stream.close()

    def open_file(target, *args, **kwargs):
        stream = original_open(target, *args, **kwargs)
        return SlowClose(stream) if target == path else stream

    monkeypatch.setattr(Path, "open", open_file)
    with FakeChiller() as chiller, ThreadPoolExecutor(2) as pool:
        chiller.start_monitoring(interval_s=0.01, csv_path=path)
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
        pending_stop = pool.submit(chiller.stop_monitoring, 0.02)
        try:
            assert closing.wait(1)
            with pytest.raises(TimeoutError, match="still owned"):
                pending_stop.result(0.5)
            snapshot = pool.submit(chiller.snapshot).result(0.5)
            assert snapshot["running"] and snapshot["logging_enabled"]
            with pytest.raises(RuntimeError, match="already started"):
                chiller.start_monitoring()
        finally:
            release.set()
        chiller.stop_monitoring()
        assert not chiller.running
        assert not chiller.snapshot()["logging_enabled"]
    path.unlink()


def test_stop_timeout_includes_lock_wait_during_pending_disconnect(monkeypatch):
    entered, release = Event(), Event()
    backend = FakeChiller()

    def pending_read():
        entered.set()
        assert release.wait(3)
        return 20

    monkeypatch.setattr(backend, "_read_temperature", pending_read)
    chiller = backend
    chiller.connect()
    with ThreadPoolExecutor(2) as pool:
        chiller.start_monitoring()
        assert entered.wait(1)
        closing = pool.submit(chiller.disconnect, 2)
        try:
            wait_for(chiller._stop.is_set)
            stopping = pool.submit(chiller.stop_monitoring, 0.02)
            with pytest.raises(TimeoutError, match="resources remain owned"):
                stopping.result(timeout=0.5)
            assert chiller.running
        finally:
            release.set()
            closing.result(timeout=2)
            chiller.disconnect()
    assert not chiller.running and not chiller.is_connected


def test_disconnect_timeout_retains_inflight_worker_and_csv_for_retry(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    backend = FakeChiller()

    def pending_read():
        entered.set()
        assert release.wait(3)
        return 20

    monkeypatch.setattr(backend, "_read_temperature", pending_read)
    chiller = backend
    chiller.connect()
    path = tmp_path / "pending-disconnect.csv"
    chiller.start_monitoring(csv_path=path)
    try:
        assert entered.wait(1)
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(TimeoutError, match="resources remain owned") as raised:
                pool.submit(chiller.disconnect, 0.02).result(timeout=0.5)
        assert chiller.running and chiller.snapshot()["logging_enabled"]
        assert chiller._cancel.is_set()
        assert any("Monitor cleanup failed" in note for note in raised.value.__notes__)
    finally:
        release.set()
        chiller.disconnect()
    assert not chiller.running and (not chiller.snapshot()["logging_enabled"])
    path.unlink()


def test_stop_uses_one_budget_for_lock_wait_and_join(monkeypatch):
    entered, release, holding = Event(), Event(), Event()
    backend = FakeChiller()

    def pending_read():
        entered.set()
        assert release.wait(3)
        return 20

    monkeypatch.setattr(backend, "_read_temperature", pending_read)
    chiller = backend
    chiller.connect()
    chiller.start_monitoring()
    assert entered.wait(1)

    def hold_monitor_lock():
        with chiller._monitor_lock:
            holding.set()
            sleep(0.15)

    holder = Thread(target=hold_monitor_lock)
    holder.start()
    try:
        assert holding.wait(1)
        started = monotonic()
        with pytest.raises(TimeoutError, match="still owned"):
            chiller.stop_monitoring(0.2)
        assert monotonic() - started < 0.3
        assert chiller.running
    finally:
        release.set()
        holder.join(1)
        chiller.disconnect()


def test_backend_close_error_survives_monitor_join_timeout(monkeypatch):
    closing, release = Event(), Event()
    backend = FakeChiller()
    chiller = backend
    chiller.connect()
    chiller.start_monitoring()
    close_csv, disconnect = (chiller._close_csv, backend._close_port)

    def pending_close():
        closing.set()
        assert release.wait(3)
        close_csv()

    def failed_disconnect():
        raise OSError("Primary backend close failure")

    monkeypatch.setattr(chiller, "_close_csv", pending_close)
    monkeypatch.setattr(backend, "_close_port", failed_disconnect)
    try:
        with pytest.raises(OSError, match="Primary backend close failure") as raised:
            chiller.disconnect(0.05)
        assert closing.is_set()
        assert any(
            "Monitor cleanup failed: TimeoutError" in note for note in raised.value.__notes__
        )
    finally:
        release.set()
        monkeypatch.setattr(backend, "_close_port", disconnect)
        chiller.disconnect()


def test_controller_owns_empty_and_running_snapshots():
    chiller = FakeChiller()
    before = chiller.snapshot()
    assert not before["running"] and (not before["history"]) and (not before["logging_enabled"])
    assert before["sample_count"] == before["failed_samples"] == before["logged_samples"] == 0
    chiller.stop_monitoring()
    with chiller:
        assert chiller.start_monitoring(interval_s=0.01) is None
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
    assert not chiller.snapshot()["running"] and (not before["history"])


def test_failed_restart_preserves_previous_run_snapshot(tmp_path):
    path = tmp_path / "existing.csv"
    path.write_text("do not overwrite", encoding="utf-8")
    with FakeChiller() as chiller:
        chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
        chiller.stop_monitoring()
        previous = chiller.snapshot()
        with pytest.raises(FileExistsError):
            chiller.start_monitoring(csv_path=path)
        assert chiller.snapshot() == previous
        assert path.read_text(encoding="utf-8") == "do not overwrite"


def test_snapshot_dictionaries_are_detached_at_every_mutable_level():
    with FakeChiller() as chiller:
        chiller.start_monitoring(interval_s=0.01)
        wait_for(lambda: chiller.snapshot()["sample_count"] > 0)
        chiller.stop_monitoring()
        original = chiller.snapshot()
        changed = chiller.snapshot()
        assert type(changed) is dict and type(changed["history"]) is list
        assert type(changed["history"][-1]) is dict
        assert type(changed["history"][-1]["status"]) is dict
        changed["service_error"] = "caller edit"
        changed["history"][-1]["temperature_c"] = -999
        changed["history"][-1]["status"]["connected"] = False
        assert chiller.snapshot() == original
        changed["history"].clear()
        assert chiller.snapshot() == original


@pytest.mark.parametrize("start_failure", [None, "header", "thread", "row"])
def test_csv_close_failure_retains_handle_refuses_restart_and_allows_cleanup_retry(
    start_failure, monkeypatch, tmp_path
):
    original_open = Path.open
    streams = []
    primary = RuntimeError("Original startup failure")

    class FileWithFailedClose:
        fail_close = True

        def __init__(self, stream):
            self.stream = stream
            self.writes = 0

        def write(self, row):
            self.writes += 1
            if start_failure == "header":
                raise primary
            if start_failure == "row" and self.writes > 1:
                raise OSError("CSV row failed")
            return self.stream.write(row)

        def flush(self):
            self.stream.flush()

        def close(self):
            if self.fail_close:
                raise OSError("CSV handle still open")
            self.stream.close()

    def open_csv(path, *args, **kwargs):
        wrapped = FileWithFailedClose(original_open(path, *args, **kwargs))
        streams.append(wrapped)
        return wrapped

    def failed_thread_start(thread):
        raise primary

    monkeypatch.setattr(Path, "open", open_csv)
    if start_failure == "thread":
        monkeypatch.setattr(Thread, "start", failed_thread_start)
    path = tmp_path / "owned.csv"
    with FakeChiller() as chiller:
        try:
            if start_failure in ("header", "thread"):
                with pytest.raises(RuntimeError) as raised:
                    chiller.start_monitoring(csv_path=path)
                assert raised.value is primary
                assert "CSV handle still open" in primary.__notes__[0]
            else:
                chiller.start_monitoring(interval_s=0.01, csv_path=path)
                wait_for(lambda: chiller.snapshot()["sample_count"] >= 3)
                if start_failure == "row":
                    assert streams[0].writes == 2  # Never write again after recording fails.
                    assert "CSV row failed" in chiller.snapshot()["logging_error"]
            with pytest.raises(OSError, match="CSV handle still open"):
                chiller.stop_monitoring()
            assert not chiller.running and chiller._file is streams[0]
            assert not streams[0].stream.closed
            with pytest.raises(OSError, match="CSV handle still open"):
                chiller.start_monitoring(csv_path=tmp_path / "replacement.csv")
            assert not (tmp_path / "replacement.csv").exists()
        finally:
            streams[0].fail_close = False
            chiller.stop_monitoring()
        assert chiller._file is None and streams[0].stream.closed
        assert "CSV handle still open" in chiller.snapshot()["logging_error"]
    path.unlink()
