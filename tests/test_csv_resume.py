"""TEST-015: whole-file CSV validation, explicit resume and recording lifecycle."""

import csv
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from threading import Event

import pytest

from tark_chiller.csvlog import CsvLogger
from tark_chiller.device import DeviceStatus
from tark_chiller.monitoring import Sample


@pytest.fixture
def sample():
    return Sample(
        datetime(2026, 9, 10, 12, 30, tzinfo=UTC),
        0.0,
        20.5,
        18.0,
        DeviceStatus(True, "simulator", "Connected"),
    )


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_append_twice_preserves_bytes_and_identifies_elapsed_resets(tmp_path, sample):
    path = tmp_path / "nested" / "run" / "data.csv"
    with CsvLogger(path, session_id="first") as logger:
        assert logger.path == path and logger.session_id == "first"
        assert logger.is_open and logger.rows_written == 0
        logger.write(sample)
        logger.write(replace(sample, elapsed_s=10))
        assert logger.rows_written == 2
        assert len(read_rows(path)) == 2  # Every row visible before close.
    previous = path.read_bytes()
    assert not logger.is_open
    for session in ("second", "third"):
        with CsvLogger(path, append=True, session_id=session) as logger:
            assert logger.rows_written == 0
            logger.write(sample)
            assert logger.rows_written == 1
        assert path.read_bytes().startswith(previous)
        previous = path.read_bytes()
    rows = read_rows(path)
    assert [row["session_id"] for row in rows] == ["first", "first", "second", "third"]
    assert [float(row["elapsed_s"]) for row in rows] == [0, 10, 0, 0]
    assert path.read_text().count(",".join(CsvLogger.FIELDS)) == 1


@pytest.mark.parametrize("existing", [False, True])
def test_append_creates_header_for_missing_or_empty_file(tmp_path, sample, existing):
    path = tmp_path / "new.csv"
    if existing:
        path.touch()
    with CsvLogger(path, append=True) as logger:
        logger.write(sample)
    assert len(read_rows(path)) == 1
    assert path.read_text().count(",".join(CsvLogger.FIELDS)) == 1


def test_automatic_ids_are_distinct_and_existing_id_cannot_be_reused(tmp_path, sample):
    path = tmp_path / "sessions.csv"
    with CsvLogger(path) as first:
        first.write(sample)
    with CsvLogger(path, append=True) as second:
        second.write(sample)
    assert first.session_id != second.session_id
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        CsvLogger(path, append=True, session_id=first.session_id)
    assert path.read_bytes() == before


def test_default_does_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "precious.csv"
    original = b"unrelated existing data\n"
    path.write_bytes(original)
    with pytest.raises(FileExistsError):
        CsvLogger(path)
    assert path.read_bytes() == original


def test_append_accepts_quoted_multiline_and_missing_error_measurements(tmp_path, sample):
    path = tmp_path / "quoted.csv"
    failed = replace(
        sample,
        temperature_c=None,
        setpoint_c=None,
        status=DeviceStatus(False, "simulator", 'lost, "communication"\nnot fresh'),
        error='Timeout: "first line"\r\nsecond line',
    )
    with CsvLogger(path) as logger:
        logger.write(failed)
    with CsvLogger(path, append=True) as logger:
        logger.write(sample)
    rows = read_rows(path)
    assert rows[0]["status_detail"] == failed.status.detail
    assert rows[0]["error"] == failed.error
    assert rows[0]["temperature_c"] == rows[0]["setpoint_c"] == ""
    assert rows[0]["connected"] == "False"
    assert rows[1]["error"] == ""


@pytest.mark.parametrize(
    "damage",
    [
        "old_header",
        "duplicate_header",
        "extra_field",
        "missing_field",
        "blank_record",
        "torn_quoted_record",
        "missing_final_newline",
        "invalid_utf8",
    ],
)
def test_corrupt_existing_data_is_refused_without_modification(tmp_path, sample, damage):
    path = tmp_path / "damaged.csv"
    with CsvLogger(path) as logger:
        logger.write(sample)
    original = path.read_bytes()
    if damage == "old_header":
        original = original.replace(b"session_id,", b"", 1)
    elif damage == "duplicate_header":
        original += (",".join(CsvLogger.FIELDS) + "\r\n").encode()
    elif damage == "extra_field":
        original = original[:-2] + b",extra\r\n"
    elif damage == "missing_field":
        original += b"partial,record\r\n"
    elif damage == "blank_record":
        original += b"\r\n"
    elif damage == "torn_quoted_record":
        original += b'"unterminated quoted cell\r\n'
    elif damage == "missing_final_newline":
        original = original[:-2]
    elif damage == "invalid_utf8":
        original += b"\xff\r\n"
    path.write_bytes(original)
    with pytest.raises(ValueError):
        CsvLogger(path, append=True)
    assert path.read_bytes() == original
    # Failed initialization must also release the descriptor (Windows rename).
    renamed = path.with_suffix(".rejected")
    path.rename(renamed)
    assert renamed.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timestamp_utc", "not-a-time"),
        ("timestamp_utc", "2026-09-10T12:00:00"),
        ("timestamp_utc", "2026-09-10T12:00:00+01:00"),
        ("elapsed_s", ""),
        ("elapsed_s", "-1"),
        ("elapsed_s", "NaN"),
        ("temperature_c", "NaN"),
        ("temperature_c", "Infinity"),
        ("setpoint_c", "broken"),
        ("connected", "maybe"),
        ("backend", ""),
        ("session_id", ""),
        ("session_id", "bad id"),
    ],
)
def test_resume_validates_values_in_all_completed_rows(tmp_path, sample, field, value):
    path = tmp_path / "values.csv"
    with CsvLogger(path) as logger:
        logger.write(sample)
    rows = read_rows(path)
    rows.append(dict(rows[0]))
    rows[1][field] = value
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CsvLogger.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        CsvLogger(path, append=True)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("elapsed_s", -1),
        ("elapsed_s", None),
        ("elapsed_s", True),
        ("elapsed_s", 10**400),
        ("temperature_c", float("nan")),
        ("temperature_c", "20"),
        ("setpoint_c", float("inf")),
        ("timestamp_utc", datetime(2026, 9, 10)),
        ("error", 17),
    ],
)
def test_invalid_sample_is_rejected_before_writing_and_logger_can_continue(
    tmp_path, sample, field, value
):
    path = tmp_path / "validate.csv"
    with CsvLogger(path) as logger:
        before = path.read_bytes()
        with pytest.raises(ValueError):
            logger.write(replace(sample, **{field: value}))
        assert logger.rows_written == 0
        assert path.read_bytes() == before
        logger.write(sample)
    assert len(read_rows(path)) == 1


def test_timestamp_is_normalized_to_utc(tmp_path, sample):
    path = tmp_path / "utc.csv"
    local = sample.timestamp_utc.astimezone(timezone(timedelta(hours=-5)))
    with CsvLogger(path) as logger:
        logger.write(replace(sample, timestamp_utc=local))
    assert read_rows(path)[0]["timestamp_utc"] == sample.timestamp_utc.isoformat()


@pytest.mark.parametrize("session", ["", "has spaces", "bad\nline", "/root", "a" * 129, 3])
def test_invalid_session_id_does_not_create_output(tmp_path, session):
    path = tmp_path / "new" / "output.csv"
    with pytest.raises(ValueError):
        CsvLogger(path, session_id=session)
    assert not path.parent.exists()


def test_close_is_idempotent_and_closed_write_is_explicit(tmp_path, sample):
    path = tmp_path / "close.csv"
    logger = CsvLogger(path)
    logger.write(sample)
    logger.close()
    logger.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="closed"):
        logger.write(sample)
    assert not logger.is_open and logger.rows_written == 1
    assert path.read_bytes() == before


def test_initial_header_failure_closes_descriptor(tmp_path, monkeypatch):
    from tark_chiller import csvlog

    class FailedDisk(BytesIO):
        def write(self, _data):
            raise OSError("header disk failure")

    disk = FailedDisk()
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: disk)
    monkeypatch.setattr(csvlog, "_lock_file", lambda _raw: None)
    with pytest.raises(OSError, match="header disk failure"):
        CsvLogger(tmp_path / "failed.csv")
    assert disk.closed


def test_write_failure_blocks_further_writes(tmp_path, sample, monkeypatch):
    path = tmp_path / "disk.csv"
    with CsvLogger(path) as logger:
        calls = []

        def failed_row(_row):
            calls.append(1)
            raise OSError("simulated disk full")

        monkeypatch.setattr(logger._writer, "writerow", failed_row)
        with pytest.raises(OSError, match="disk full"):
            logger.write(sample)
        with pytest.raises(ValueError, match="write failure"):
            logger.write(sample)
        assert calls == [1]
        assert logger.rows_written == 0


@pytest.mark.parametrize("stage", ["row", "flush"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_recording_refuses_reuse_without_changing_uncertain_bytes(
    tmp_path, sample, monkeypatch, stage, interruption
):
    path = tmp_path / "interrupted.csv"
    with CsvLogger(path) as logger:
        writerow = logger._writer.writerow
        flush = logger._stream.flush

        def interrupted_row(_row):
            logger._stream.write("partial record")
            flush()
            raise interruption("application interrupted the row")

        def interrupted_flush():
            flush()
            raise interruption("application interrupted the flush")

        if stage == "row":
            monkeypatch.setattr(logger._writer, "writerow", interrupted_row)
        else:
            monkeypatch.setattr(logger._stream, "flush", interrupted_flush)
        try:
            with pytest.raises(interruption, match="application interrupted"):
                logger.write(sample)
        finally:
            monkeypatch.setattr(logger._writer, "writerow", writerow)
            monkeypatch.setattr(logger._stream, "flush", flush)
        uncertain = path.read_bytes()
        assert logger.rows_written == 0
        with pytest.raises(ValueError, match="write failure"):
            logger.write(sample)
        assert path.read_bytes() == uncertain


def test_concurrent_writes_are_complete_and_close_waits_for_write(tmp_path, sample, monkeypatch):
    path = tmp_path / "concurrent.csv"
    logger = CsvLogger(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: logger.write(sample), range(40)))
    assert len(read_rows(path)) == logger.rows_written == 40

    entered, release, close_entered, close_finished = Event(), Event(), Event(), Event()
    writerow = logger._writer.writerow

    def held_write(row):
        entered.set()
        assert release.wait(2)
        return writerow(row)

    def close():
        close_entered.set()
        logger.close()
        close_finished.set()

    monkeypatch.setattr(logger._writer, "writerow", held_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(logger.write, sample)
        try:
            assert entered.wait(1)
            closing = pool.submit(close)
            assert close_entered.wait(1)
            assert not close_finished.is_set()
        finally:
            release.set()
        writing.result(2)
        closing.result(2)
    assert not logger.is_open
    assert len(read_rows(path)) == logger.rows_written == 41


def test_live_logger_refuses_second_owner_and_releases_on_close(tmp_path, sample):
    path = tmp_path / "one-owner.csv"
    with CsvLogger(path) as first:
        first.write(sample)
        before = path.read_bytes()
        with pytest.raises(OSError, match="lock CSV"):
            CsvLogger(path, append=True)
        assert path.read_bytes() == before
        first.write(sample)
        assert len(read_rows(path)) == 2  # File ownership does not block readers.
    with CsvLogger(path, append=True) as resumed:
        resumed.write(sample)
    assert len(read_rows(path)) == 3


def test_csv_lock_uses_file_identity_and_refuses_other_process(tmp_path, sample):
    path = tmp_path / "process-owner.csv"
    code = (
        "import sys\n"
        "from tark_chiller.csvlog import CsvLogger\n"
        "try:\n"
        "    logger = CsvLogger(sys.argv[1], append=True)\n"
        "except OSError as error:\n"
        "    assert 'lock CSV' in str(error), error\n"
        "else:\n"
        "    logger.close()\n"
        "    raise AssertionError('Concurrent writer acquired the live file')\n"
    )
    with CsvLogger(path) as first:
        first.write(sample)
        before = path.read_bytes()
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        alias = tmp_path / "same-file.csv"
        alias.hardlink_to(path)
        try:
            with pytest.raises(OSError, match="lock CSV"):
                CsvLogger(alias, append=True)
        finally:
            alias.unlink()
        assert path.read_bytes() == before


def test_validation_failure_releases_file_lock(tmp_path, sample):
    path = tmp_path / "validation-lock.csv"
    path.write_bytes(b"wrong,header\n")
    for _ in range(2):
        with pytest.raises(ValueError, match="header/schema"):
            CsvLogger(path, append=True)
    # A later corrected file can acquire the same OS identity.
    path.write_text(",".join(CsvLogger.FIELDS) + "\n", encoding="utf-8")
    with CsvLogger(path, append=True) as logger:
        logger.write(sample)
    assert len(read_rows(path)) == 1


def test_close_flush_failure_is_reported_and_releases_descriptor_lock(
    tmp_path, sample, monkeypatch
):
    path = tmp_path / "close-failure.csv"
    logger = CsvLogger(path)
    logger.write(sample)

    def failed_flush():
        raise OSError("injected flush failure during close")

    monkeypatch.setattr(logger._stream.buffer, "flush", failed_flush)
    with pytest.raises(OSError, match="during close"):
        logger.close()
    assert not logger.is_open
    with CsvLogger(path, append=True) as resumed:
        resumed.write(sample)
    assert len(read_rows(path)) == 2
