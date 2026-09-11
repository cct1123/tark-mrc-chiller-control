"""Flushed CSV recording with explicit, validated continuation across sessions."""

import csv
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import TextIOWrapper
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, BinaryIO, TextIO
from uuid import uuid4

if TYPE_CHECKING:
    from .monitoring import Sample


def _lock_file(raw: BinaryIO) -> None:
    """Reserve the open file until close, without blocking ordinary CSV readers."""
    try:
        if sys.platform == "win32":
            import msvcrt

            # Windows byte locks also deny readers. Reserve one byte far beyond
            # possible CSV data; locking beyond EOF does not enlarge the file.
            raw.seek((1 << 63) - 2)
            msvcrt.locking(raw.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(raw.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise OSError(f"Cannot lock CSV for writing (another writer may own it): {exc}") from exc


def _session_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is None
    ):
        raise ValueError(
            "session_id must contain 1–128 letters, digits, dots, underscores or hyphens"
        )
    return value


def _number(value: object, name: str, *, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (name == "elapsed_s" and value < 0):
        raise ValueError(
            f"{name} must be finite" + (" and nonnegative" if name == "elapsed_s" else "")
        )
    return str(value)


class CsvLogger:
    """One owner per file, with thread-safe writes and idempotent close.

    By default an existing path is refused. ``append=True`` verifies the complete
    existing file before the first write, without repairing damaged records.
    Each logger has a distinct session ID because elapsed time may restart.
    A flush makes rows visible to readers; it is not a power-loss durability
    guarantee. An OS file lock refuses concurrent CsvLogger owners, including
    other processes. Writers that ignore the lock are outside this contract.
    """

    FIELDS = (
        "session_id",
        "timestamp_utc",
        "elapsed_s",
        "temperature_c",
        "setpoint_c",
        "backend",
        "connected",
        "status_detail",
        "error",
    )

    def __init__(
        self,
        path: str | Path,
        *,
        append: bool = False,
        session_id: str | None = None,
    ) -> None:
        if not isinstance(append, bool):
            raise ValueError("append must be a boolean")
        self._session_id = _session_id(uuid4().hex if session_id is None else session_id)
        self._path = Path(path)
        self._lock = Lock()
        self._rows_written = 0
        self._failed = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Validate and append through the same descriptor, avoiding a path switch
        # between validation and opening the output. Opening does not change bytes.
        raw = self._path.open("a+b" if append else "x+b")
        stream: TextIOWrapper | None = None
        try:
            _lock_file(raw)
            size = raw.seek(0, 2)
            if size:
                raw.seek(-1, 2)
                if raw.read(1) != b"\n":
                    raise ValueError(
                        "CSV has an unterminated final record; existing bytes preserved"
                    )
            raw.seek(0)
            stream = TextIOWrapper(raw, newline="", encoding="utf-8")
            if size:
                self._validate_existing(stream)
            self._writer = csv.DictWriter(stream, fieldnames=self.FIELDS)
            if not size:
                self._writer.writeheader()
                stream.flush()
        except BaseException:
            with suppress(Exception):
                (stream if stream is not None else raw).close()
            raise
        self._stream = stream

    def _validate_existing(self, stream: TextIO) -> None:
        try:
            reader = csv.reader(stream, strict=True)
            if tuple(next(reader)) != self.FIELDS:
                raise ValueError(
                    "CSV header/schema mismatch; start a new file or use the matching schema"
                )
            for row in reader:
                label = f"CSV record ending at line {reader.line_num}"
                if len(row) != len(self.FIELDS):
                    raise ValueError(f"{label}: expected {len(self.FIELDS)} fields, got {len(row)}")
                entry = dict(zip(self.FIELDS, row, strict=True))
                _session_id(entry["session_id"])
                if entry["session_id"] == self._session_id:
                    raise ValueError(
                        "session_id already exists in CSV; each resumed run needs a new ID"
                    )
                try:
                    timestamp = datetime.fromisoformat(entry["timestamp_utc"])
                except ValueError as exc:
                    raise ValueError(f"{label}: invalid timestamp_utc") from exc
                if timestamp.utcoffset() != timedelta(0):
                    raise ValueError(f"{label}: timestamp_utc must include a UTC offset")
                for name in ("elapsed_s", "temperature_c", "setpoint_c"):
                    if not entry[name] and name != "elapsed_s":
                        continue
                    try:
                        _number(float(entry[name]), name)
                    except ValueError as exc:
                        raise ValueError(f"{label}: invalid {name}") from exc
                if not entry["backend"] or entry["connected"] not in ("True", "False"):
                    raise ValueError(f"{label}: invalid backend or connected status")
        except (csv.Error, UnicodeError, StopIteration) as exc:
            raise ValueError(f"Invalid CSV; existing bytes preserved: {exc}") from exc

    @property
    def path(self) -> Path:
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_open(self) -> bool:
        with self._lock:
            return not self._stream.closed

    @property
    def rows_written(self) -> int:
        """Successfully flushed data rows in this logger's session."""
        with self._lock:
            return self._rows_written

    def write(self, sample: "Sample") -> None:
        with self._lock:
            if self._stream.closed:
                raise ValueError("CSV logger is closed")
            if self._failed:
                raise ValueError("CSV logger had a write failure; inspect the file before resuming")
            if (
                not isinstance(sample.timestamp_utc, datetime)
                or sample.timestamp_utc.utcoffset() is None
            ):
                raise ValueError("timestamp_utc must be a timezone-aware datetime")
            if (
                not isinstance(sample.status.connected, bool)
                or not isinstance(sample.status.backend, str)
                or not sample.status.backend
                or not isinstance(sample.status.detail, str)
                or (sample.error is not None and not isinstance(sample.error, str))
            ):
                raise ValueError("CSV status and error fields have invalid types")
            row = {
                "session_id": self._session_id,
                "timestamp_utc": sample.timestamp_utc.astimezone(UTC).isoformat(),
                "elapsed_s": _number(sample.elapsed_s, "elapsed_s"),
                "temperature_c": _number(sample.temperature_c, "temperature_c", optional=True),
                "setpoint_c": _number(sample.setpoint_c, "setpoint_c", optional=True),
                "backend": sample.status.backend,
                "connected": sample.status.connected,
                "status_detail": sample.status.detail,
                "error": sample.error,
            }
            try:
                self._writer.writerow(row)
                self._stream.flush()
                self._rows_written += 1
            except BaseException:
                self._failed = True
                raise

    def close(self) -> None:
        with self._lock:
            self._stream.close()

    def __enter__(self) -> "CsvLogger":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
