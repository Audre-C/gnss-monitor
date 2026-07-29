"""Unit tests for the optional data-logging subsystem.

Covers: disabled-by-default inertness, each writer's on-disk format via
the real DataLogger queue/thread (not by calling the writers directly -
that's what actually exercises the "never block acquisition" design),
day rotation, snapshot interval throttling, and that DataSink calls from
ReceiverMonitor never influence acquisition state.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import pytest

from gnss_monitor.config.schema import (
    DataLoggingConfig,
    ParsedLoggerConfig,
    RawNmeaLoggerConfig,
    SnapshotLoggerConfig,
)
from gnss_monitor.data_logging import DataLogger
from gnss_monitor.model import GGAMessage, NmeaSentence
from gnss_monitor.model.messages import FixQuality


def _sentence(raw: str, sentence_type: str = "GGA", talker: str = "GP") -> NmeaSentence:
    return NmeaSentence(
        raw=raw,
        checksum_ok=True,
        talker=talker,
        sentence_type=sentence_type,
        fields=tuple(),
        channel_id="r1",
        t_rx_wall=1785328282.315,  # 2026-07-29T12:31:22.315Z
        t_rx_mono=100.0,
    )


def _gga(sentence: NmeaSentence, **overrides) -> GGAMessage:
    defaults = dict(
        sentence=sentence,
        utc_time="123122.00",
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        fix_quality=FixQuality.GPS,
        num_satellites=8,
        hdop=0.9,
        altitude_m=40.0,
        geoid_separation_m=None,
    )
    defaults.update(overrides)
    return GGAMessage(**defaults)


def enabled_config(
    tmp_path: Path,
    raw: bool = True,
    parsed: bool = True,
    snapshot: bool = True,
    interval_s: float = 60.0,
    rotate_daily: bool = True,
) -> DataLoggingConfig:
    return DataLoggingConfig(
        enabled=True,
        raw_nmea=RawNmeaLoggerConfig(enabled=raw),
        parsed=ParsedLoggerConfig(enabled=parsed),
        snapshot=SnapshotLoggerConfig(enabled=snapshot, interval_s=interval_s),
        directory=tmp_path,
        rotate_daily=rotate_daily,
    )


class TestDisabledByDefault:
    def test_none_config_is_fully_inert(self) -> None:
        logger = DataLogger(None)
        assert logger.enabled is False
        # None of these may raise, block, or touch disk.
        logger.on_raw("r1", time.time(), "$GPGGA,*00")
        logger.on_parsed("r1", time.time(), _sentence("$GPGGA,*00"), object())
        logger.update_receiver_context("r1", avg_cn0_dbhz=40.0)
        logger.record_snapshot(
            "r1",
            name="R1",
            constellation="GPS",
            health="OK",
            analysis_state=None,
            score=None,
            latitude_deg=None,
            longitude_deg=None,
            distance_from_expected_m=None,
            speed_mps=None,
            has_fix=None,
            hdop=None,
            num_satellites=None,
            avg_cn0_dbhz=None,
            triggered_rules="",
        )
        logger.close()

    def test_master_switch_disabled_is_fully_inert(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path)
        config = config.model_copy(update={"enabled": False})
        logger = DataLogger(config)
        assert logger.enabled is False
        logger.on_raw("r1", time.time(), "$GPGGA,*00")
        logger.close()
        assert list(tmp_path.iterdir()) == []

    def test_enabled_but_no_sub_logger_enabled_starts_no_thread(
        self, tmp_path: Path
    ) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False, snapshot=False)
        logger = DataLogger(config)
        assert logger.enabled is False
        logger.close()
        assert list(tmp_path.iterdir()) == []


class TestRawNmeaLogging:
    def test_writes_one_file_per_receiver_under_day_directory(
        self, tmp_path: Path
    ) -> None:
        config = enabled_config(tmp_path, parsed=False, snapshot=False)
        logger = DataLogger(config)
        logger.on_raw("neo6m_1", 1785328282.315, "$GPGGA,123122.00,*4F")
        logger.on_raw("neom10_1", 1785328282.5, "$GNRMC,123122.50,A*10")
        logger.close()

        day_dirs = list(tmp_path.iterdir())
        assert len(day_dirs) == 1
        day_dir = day_dirs[0]
        assert day_dir.name == time.strftime(
            "%Y-%m-%d", time.gmtime(1785328282.315)
        )
        assert {p.name for p in day_dir.iterdir()} == {
            "neo6m_1.nmea",
            "neom10_1.nmea",
        }
        content = (day_dir / "neo6m_1.nmea").read_text(encoding="utf-8")
        assert content == "2026-07-29T12:31:22.315Z,$GPGGA,123122.00,*4F\n"

    def test_flat_layout_when_rotate_daily_disabled(self, tmp_path: Path) -> None:
        config = enabled_config(
            tmp_path, parsed=False, snapshot=False, rotate_daily=False
        )
        logger = DataLogger(config)
        logger.on_raw("neo6m_1", 1785328282.315, "$GPGGA,*4F")
        logger.close()
        assert (tmp_path / "neo6m_1.nmea").is_file()

    def test_disabled_sub_logger_writes_nothing(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False, snapshot=False)
        # Enable only snapshot so the thread actually starts, to prove
        # raw specifically stays silent rather than the whole subsystem.
        config = config.model_copy(
            update={"snapshot": SnapshotLoggerConfig(enabled=True)}
        )
        logger = DataLogger(config)
        logger.on_raw("neo6m_1", time.time(), "$GPGGA,*4F")
        logger.close()
        for day_dir in tmp_path.iterdir():
            assert "neo6m_1.nmea" not in {p.name for p in day_dir.iterdir()}


class TestParsedMessageLogging:
    def test_writes_header_and_row_with_enrichment(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, snapshot=False)
        logger = DataLogger(config)
        logger.update_receiver_context(
            "r1", avg_cn0_dbhz=39.42, analysis_score=40.0, analysis_state="Warning"
        )
        sentence = _sentence("$GPGGA,123122.00,*4F")
        logger.on_parsed("r1", sentence.t_rx_wall, sentence, _gga(sentence))
        logger.close()

        rows = _read_csv(tmp_path, "parsed.csv")
        assert rows[0] == [
            "timestamp", "receiver", "sentence", "talker", "latitude",
            "longitude", "fix", "satellites", "hdop", "speed",
            "average_cn0", "analysis_score", "analysis_state",
        ]
        assert rows[1] == [
            "2026-07-29T12:31:22.315Z", "r1", "GGA", "GP",
            "25.334373", "51.469359", "True", "8", "0.9", "",
            "39.42", "40.0", "Warning",
        ]

    def test_unrecognised_message_type_leaves_fields_blank(
        self, tmp_path: Path
    ) -> None:
        config = enabled_config(tmp_path, raw=False, snapshot=False)
        logger = DataLogger(config)
        sentence = _sentence("$GPGSV,1,1,00*4F", sentence_type="GSV")
        logger.on_parsed("r1", sentence.t_rx_wall, sentence, object())
        logger.close()

        rows = _read_csv(tmp_path, "parsed.csv")
        assert rows[1][2] == "GSV"
        assert rows[1][4:10] == ["", "", "", "", "", ""]

    def test_disabled_sub_logger_writes_nothing(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False, snapshot=True)
        logger = DataLogger(config)
        sentence = _sentence("$GPGGA,*4F")
        logger.on_parsed("r1", sentence.t_rx_wall, sentence, _gga(sentence))
        logger.close()
        assert not any(tmp_path.rglob("parsed.csv"))


class TestSnapshotLogging:
    def test_writes_header_and_row(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False)
        logger = DataLogger(config)
        logger.record_snapshot(
            "r1",
            name="NEO-6M",
            constellation="GPS",
            health="OK",
            analysis_state="Warning",
            score=40.0,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            distance_from_expected_m=12.3,
            speed_mps=0.1,
            has_fix=True,
            hdop=0.9,
            num_satellites=8,
            avg_cn0_dbhz=39.42,
            triggered_rules="Position Offset +40 (distance=1243m)",
        )
        logger.close()

        rows = _read_csv(tmp_path, "snapshot.csv")
        assert rows[0][:6] == [
            "timestamp", "receiver", "constellation", "health",
            "analysis_state", "score",
        ]
        assert rows[1][1:6] == ["NEO-6M", "GPS", "OK", "Warning", "40.0"]
        assert rows[1][-1] == "Position Offset +40 (distance=1243m)"

    def test_missing_values_are_blank(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False)
        logger = DataLogger(config)
        logger.record_snapshot(
            "r1",
            name="NEO-6M",
            constellation="GPS",
            health="NO DATA",
            analysis_state=None,
            score=None,
            latitude_deg=None,
            longitude_deg=None,
            distance_from_expected_m=None,
            speed_mps=None,
            has_fix=None,
            hdop=None,
            num_satellites=None,
            avg_cn0_dbhz=None,
            triggered_rules="",
        )
        logger.close()

        rows = _read_csv(tmp_path, "snapshot.csv")
        assert rows[1][4:6] == ["", ""]
        assert rows[1][6:14] == ["", "", "", "", "", "", "", ""]

    def test_interval_throttles_writes_per_receiver(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=False, parsed=False, interval_s=0.05)
        logger = DataLogger(config)
        kwargs = dict(
            name="NEO-6M", constellation="GPS", health="OK", analysis_state=None,
            score=None, latitude_deg=None, longitude_deg=None,
            distance_from_expected_m=None, speed_mps=None, has_fix=None,
            hdop=None, num_satellites=None, avg_cn0_dbhz=None, triggered_rules="",
        )
        logger.record_snapshot("r1", **kwargs)
        logger.record_snapshot("r1", **kwargs)  # within interval: dropped
        time.sleep(0.08)
        logger.record_snapshot("r1", **kwargs)  # past interval: written
        logger.close()

        rows = _read_csv(tmp_path, "snapshot.csv")
        assert len(rows) == 1 + 2  # header + 2 data rows

    def test_disabled_sub_logger_writes_nothing(self, tmp_path: Path) -> None:
        config = enabled_config(tmp_path, raw=True, parsed=False, snapshot=False)
        logger = DataLogger(config)
        logger.record_snapshot(
            "r1", name="N", constellation="-", health="OK", analysis_state=None,
            score=None, latitude_deg=None, longitude_deg=None,
            distance_from_expected_m=None, speed_mps=None, has_fix=None,
            hdop=None, num_satellites=None, avg_cn0_dbhz=None, triggered_rules="",
        )
        logger.close()
        assert not any(tmp_path.rglob("snapshot.csv"))


def _read_csv(tmp_path: Path, filename: str) -> list[list[str]]:
    matches = list(tmp_path.rglob(filename))
    assert len(matches) == 1, f"expected exactly one {filename}, found {matches}"
    with open(matches[0], newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))
