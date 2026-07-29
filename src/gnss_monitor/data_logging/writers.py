"""Synchronous, disk-touching file writers for the data-logging subsystem.

Every class here is only ever called from DataLogger's single background
thread (see logger.py) - never from ReceiverMonitor or LiveController
directly - so none of this needs its own locking. Each writer owns its
open file handle(s) and flushes after every row: at the sentence rates
these receivers produce (a handful of lines/second each), the extra
flush() is cheap, and it means a crash or power loss loses at most the
last unflushed row rather than an entire buffer - important for a
device meant to run unattended for weeks on an SD card.

Day rotation is keyed off each record's own t_wall (UTC, matching the
"...Z" timestamps written into every row) rather than wall-clock "now"
at write time. The two usually agree, but under a disk stall or a queue
backlog spanning a UTC midnight boundary they would not - using the
record's own timestamp is what keeps a sentence filed under the day it
actually happened, rather than the day the background thread got around
to it. It also makes rotation independent of the host's local timezone
configuration.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from gnss_monitor.data_logging.records import (
    ParsedMessageRecord,
    RawSentenceRecord,
    SnapshotRecord,
)


def _iso_timestamp(t_wall: float) -> str:
    """UTC timestamp with millisecond precision, e.g. "2026-07-29T12:31:22.315Z"."""
    dt = datetime.fromtimestamp(t_wall, tz=timezone.utc)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond // 1000:03d}Z"


def _day_key(t_wall: float, rotate_daily: bool) -> str:
    if not rotate_daily:
        return ""
    return datetime.fromtimestamp(t_wall, tz=timezone.utc).strftime("%Y-%m-%d")


def _blank_if_none(value: object) -> str:
    return "" if value is None else str(value)


class RawNmeaWriter:
    """One append-only ``<receiver_id>.nmea`` file per receiver per day."""

    def __init__(self, base_dir: Path, rotate_daily: bool) -> None:
        self._base_dir = base_dir
        self._rotate_daily = rotate_daily
        self._files: dict[str, tuple[str, IO[str]]] = {}

    def write(self, record: RawSentenceRecord) -> None:
        handle = self._handle_for(record.receiver_id, record.t_wall)
        handle.write(f"{_iso_timestamp(record.t_wall)},{record.raw}\n")
        handle.flush()

    def _handle_for(self, receiver_id: str, t_wall: float) -> IO[str]:
        day_key = _day_key(t_wall, self._rotate_daily)
        cached = self._files.get(receiver_id)
        if cached is not None and cached[0] == day_key:
            return cached[1]
        if cached is not None:
            cached[1].close()
        directory = (
            self._base_dir / day_key if self._rotate_daily else self._base_dir
        )
        directory.mkdir(parents=True, exist_ok=True)
        handle = open(
            directory / f"{receiver_id}.nmea", "a", encoding="utf-8", newline=""
        )
        self._files[receiver_id] = (day_key, handle)
        return handle

    def close(self) -> None:
        for _day_key_, handle in self._files.values():
            handle.close()
        self._files.clear()


class _DailyCsvWriter:
    """Base for a single rotating CSV file shared across all receivers.

    Subclasses provide a filename and header; the header is written once
    per new file, immediately after it is created.
    """

    _FILENAME: str
    _HEADER: tuple[str, ...]

    def __init__(self, base_dir: Path, rotate_daily: bool) -> None:
        self._base_dir = base_dir
        self._rotate_daily = rotate_daily
        self._day_key: Optional[str] = None
        self._file: Optional[IO[str]] = None
        self._csv_writer: Optional["csv._writer"] = None

    def _write_row(self, row: tuple, t_wall: float) -> None:
        self._ensure_file(t_wall)
        assert self._csv_writer is not None and self._file is not None
        self._csv_writer.writerow(row)
        self._file.flush()

    def _ensure_file(self, t_wall: float) -> None:
        day_key = _day_key(t_wall, self._rotate_daily)
        if self._file is not None and day_key == self._day_key:
            return
        if self._file is not None:
            self._file.close()
        directory = (
            self._base_dir / day_key if self._rotate_daily else self._base_dir
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self._FILENAME
        is_new = not path.exists()
        self._file = open(path, "a", encoding="utf-8", newline="")
        self._csv_writer = csv.writer(self._file)
        if is_new:
            self._csv_writer.writerow(self._HEADER)
            self._file.flush()
        self._day_key = day_key

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._csv_writer = None


class ParsedCsvWriter(_DailyCsvWriter):
    """One ``parsed.csv`` per day, one row per parsed NMEA sentence."""

    _FILENAME = "parsed.csv"
    _HEADER = (
        "timestamp",
        "receiver",
        "sentence",
        "talker",
        "latitude",
        "longitude",
        "fix",
        "satellites",
        "hdop",
        "speed",
        "average_cn0",
        "analysis_score",
        "analysis_state",
    )

    def write(self, record: ParsedMessageRecord) -> None:
        self._write_row(
            (
                _iso_timestamp(record.t_wall),
                record.receiver_id,
                record.sentence_type or "",
                record.talker or "",
                _blank_if_none(record.latitude_deg),
                _blank_if_none(record.longitude_deg),
                _blank_if_none(record.has_fix),
                _blank_if_none(record.num_satellites),
                _blank_if_none(record.hdop),
                _blank_if_none(record.speed_mps),
                _blank_if_none(record.avg_cn0_dbhz),
                _blank_if_none(record.analysis_score),
                record.analysis_state or "",
            ),
            record.t_wall,
        )


class SnapshotCsvWriter(_DailyCsvWriter):
    """One ``snapshot.csv`` per day, one row per receiver per interval tick."""

    _FILENAME = "snapshot.csv"
    _HEADER = (
        "timestamp",
        "receiver",
        "constellation",
        "health",
        "analysis_state",
        "score",
        "latitude",
        "longitude",
        "distance_from_expected",
        "speed",
        "fix",
        "hdop",
        "satellites",
        "average_cn0",
        "triggered_rules",
    )

    def write(self, record: SnapshotRecord) -> None:
        self._write_row(
            (
                _iso_timestamp(record.t_wall),
                record.name,
                record.constellation,
                record.health,
                record.analysis_state or "",
                _blank_if_none(record.score),
                _blank_if_none(record.latitude_deg),
                _blank_if_none(record.longitude_deg),
                _blank_if_none(record.distance_from_expected_m),
                _blank_if_none(record.speed_mps),
                _blank_if_none(record.has_fix),
                _blank_if_none(record.hdop),
                _blank_if_none(record.num_satellites),
                _blank_if_none(record.avg_cn0_dbhz),
                record.triggered_rules,
            ),
            record.t_wall,
        )
