"""One CSV per run, with explicit units and missing values preserved."""

import csv
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .monitoring import Sample


class CsvLogger:
    FIELDS = (
        "timestamp_utc",
        "elapsed_s",
        "temperature_c",
        "setpoint_c",
        "backend",
        "connected",
        "status_detail",
        "error",
    )

    def __init__(self, path: str | Path):
        self._stream = Path(path).open("x", newline="", encoding="utf-8")
        try:
            self._writer = csv.DictWriter(self._stream, fieldnames=self.FIELDS)
            self._writer.writeheader()
            self._stream.flush()
        except Exception:
            self._stream.close()
            raise

    def write(self, sample: "Sample") -> None:
        self._writer.writerow(
            {
                "timestamp_utc": sample.timestamp_utc.astimezone(UTC).isoformat(),
                "elapsed_s": sample.elapsed_s,
                "temperature_c": sample.temperature_c,
                "setpoint_c": sample.setpoint_c,
                "backend": sample.status.backend,
                "connected": sample.status.connected,
                "status_detail": sample.status.detail,
                "error": sample.error,
            }
        )
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "CsvLogger":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
