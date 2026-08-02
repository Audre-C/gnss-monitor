"""Unit tests for ReceiverMonitor position extraction from real captures."""

from __future__ import annotations

import time
from typing import Optional

import pytest

from gnss_monitor.model import SatelliteInfo
from gnss_monitor.model.sentence import NmeaSentence
from gnss_monitor.monitor import ReceiverMonitor
from gnss_monitor.monitor.receiver_monitor import _CN0_TRACK_TTL_S, ReceiverState
from gnss_monitor.sources import FileReplaySource
from tests.fixtures import dataset_path


def drain(receiver: str) -> ReceiverMonitor:
    src = FileReplaySource(receiver, dataset_path(receiver, "normal"))
    monitor = ReceiverMonitor(receiver, receiver, src)
    src.open()
    while not src.is_exhausted:
        if monitor.poll(1000) == 0:
            break
    src.close()
    return monitor


def test_neom10_extracts_fixed_position() -> None:
    m = drain("neom10")
    assert m.state.sentences_seen > 0
    assert m.state.has_fix is True
    assert m.state.latitude_deg is not None
    assert 25.0 < m.state.latitude_deg < 26.0
    assert m.state.longitude_deg is not None
    assert 51.0 < m.state.longitude_deg < 52.0


def test_all_receivers_agree_on_location() -> None:
    positions = []
    for rx in ("neo6m", "lc29hea", "neom10"):
        m = drain(rx)
        assert m.state.has_fix is True
        positions.append((m.state.latitude_deg, m.state.longitude_deg))
    lats = [p[0] for p in positions]
    lons = [p[1] for p in positions]
    # All three fixtures are the same physical site: agree to <0.001 deg.
    assert max(lats) - min(lats) < 0.001
    assert max(lons) - min(lons) < 0.001


def test_checksum_counting() -> None:
    m = drain("neo6m")
    # Every line in the corpus has a valid checksum.
    assert m.state.valid_checksums == m.state.sentences_seen


def test_valid_rmc_populates_speed() -> None:
    m = ReceiverMonitor("r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal")))
    m._process_line(  # noqa: SLF001 - exercising line handling directly
        "$GNRMC,113954.00,A,2520.06189,N,05128.16076,E,0.011,,"
        "200726,,,A,V*15"
    )
    assert m.state.speed_mps == pytest.approx(0.011 * 0.514444)


def test_void_rmc_does_not_set_speed() -> None:
    m = ReceiverMonitor("r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal")))
    m._process_line("$GPRMC,,V,,,,,,,,,,N*53")  # noqa: SLF001
    assert m.state.speed_mps is None


class TestGsvTrackedCn0:
    def test_tracks_only_satellites_with_populated_snr(self) -> None:
        # PRN 4, 9, 26 report an SNR (tracked); PRN 16 is in view but
        # untracked (blank SNR field) and must not appear.
        m = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal"))
        )
        m._process_line(  # noqa: SLF001
            "$GPGSV,2,1,08,04,42,298,34,09,14,275,31,16,22,134,,26,32,101,34,1*00"
        )
        assert sorted(m.state.tracked_cn0_dbhz(time.monotonic())) == [31, 34, 34]

    def test_works_without_a_fix(self) -> None:
        # C/N0 capture must not depend on has_fix - GSV is reported
        # during a no-fix cold start too.
        m = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal"))
        )
        m._process_line("$GPGSV,1,1,01,04,42,298,34*00")  # noqa: SLF001
        assert m.state.has_fix is False
        assert m.state.tracked_cn0_dbhz(time.monotonic()) == (34,)

    def test_accumulates_across_multiple_constellation_talkers(self) -> None:
        m = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal"))
        )
        m._process_line("$GPGSV,1,1,01,04,42,298,34*00")  # noqa: SLF001
        m._process_line("$GLGSV,1,1,01,65,10,100,40*00")  # noqa: SLF001
        assert sorted(m.state.tracked_cn0_dbhz(time.monotonic())) == [34, 40]

    def test_entries_older_than_ttl_are_evicted(self) -> None:
        state = ReceiverState()
        state.record_tracked_satellites(
            "GP",
            (SatelliteInfo(prn=1, elevation_deg=10, azimuth_deg=20, snr_dbhz=30),),
            t_rx_mono=100.0,
        )
        # Still within the TTL just after the update.
        assert state.tracked_cn0_dbhz(100.0 + _CN0_TRACK_TTL_S - 1.0) == (30,)

        # A later GSV update, once the first has aged past the TTL,
        # evicts it from the tracked set.
        state.record_tracked_satellites(
            "GP",
            (SatelliteInfo(prn=2, elevation_deg=15, azimuth_deg=25, snr_dbhz=40),),
            t_rx_mono=100.0 + _CN0_TRACK_TTL_S + 1.0,
        )
        assert state.tracked_cn0_dbhz(100.0 + _CN0_TRACK_TTL_S + 1.0) == (40,)


class _RecordingSink:
    """Test double for monitor.receiver_monitor.DataSink: records every
    call it receives without influencing anything, proving ReceiverMonitor
    treats the sink purely as a passive observer."""

    def __init__(self) -> None:
        self.raw_calls: list[tuple] = []
        self.parsed_calls: list[tuple] = []

    def on_raw(self, receiver_id: str, t_wall: Optional[float], raw: str) -> None:
        self.raw_calls.append((receiver_id, t_wall, raw))

    def on_parsed(
        self,
        receiver_id: str,
        t_wall: Optional[float],
        sentence: NmeaSentence,
        message: object,
    ) -> None:
        self.parsed_calls.append((receiver_id, t_wall, sentence, message))


class TestDataSinkObservation:
    def test_sink_observes_every_line_without_affecting_state(self) -> None:
        sink = _RecordingSink()
        m = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal")),
            sink=sink,
        )
        line = "$GPGGA,123122.00,2520.06239,N,05128.16155,E,1,08,0.9,10.0,M,,,,*4F"
        m._process_line(line)  # noqa: SLF001 - exercising line handling directly

        assert len(sink.raw_calls) == 1
        receiver_id, t_wall, raw = sink.raw_calls[0]
        assert receiver_id == "r1"
        assert raw == line
        assert t_wall is not None

        assert len(sink.parsed_calls) == 1
        receiver_id, t_wall, sentence, message = sink.parsed_calls[0]
        assert receiver_id == "r1"
        assert sentence.sentence_type == "GGA"
        assert message is not None

        # State updates are identical to the no-sink path: the sink is
        # a pure observer, not a participant.
        assert m.state.has_fix is True
        assert m.state.latitude_deg is not None

    def test_no_sink_behaves_identically_to_a_sink(self) -> None:
        line = "$GPGGA,123122.00,2520.06239,N,05128.16155,E,1,08,0.9,10.0,M,,,,*4F"
        without_sink = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal"))
        )
        with_sink = ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal")),
            sink=_RecordingSink(),
        )
        without_sink._process_line(line)  # noqa: SLF001
        with_sink._process_line(line)  # noqa: SLF001

        assert without_sink.state.has_fix == with_sink.state.has_fix
        assert without_sink.state.latitude_deg == with_sink.state.latitude_deg
        assert without_sink.state.longitude_deg == with_sink.state.longitude_deg
        assert without_sink.state.sentences_seen == with_sink.state.sentences_seen


class TestFixLossIsNotLatched:
    """Regression tests: a receiver that reports losing its fix must have
    has_fix (and the position it gates) reflect that immediately, not hold
    a stale True from whenever the fix was last valid - see _apply()."""

    _VALID_GGA = (
        "$GPGGA,123122.00,2520.06239,N,05128.16155,E,1,08,0.9,10.0,M,,,,*0D"
    )
    _NO_FIX_GGA = (
        "$GPGGA,123123.00,,,,,0,00,99.99,,M,,,,*2B"
    )
    _VALID_RMC = (
        "$GNRMC,113954.00,A,2520.06189,N,05128.16076,E,0.011,,200726,,,A,V*15"
    )
    _VOID_RMC = "$GPRMC,,V,,,,,,,,,,N*53"

    def _monitor(self) -> ReceiverMonitor:
        return ReceiverMonitor(
            "r1", "R1", FileReplaySource("r1", dataset_path("neo6m", "normal"))
        )

    def test_gga_fix_loss_clears_has_fix_and_position(self) -> None:
        m = self._monitor()
        m._process_line(self._VALID_GGA)  # noqa: SLF001
        assert m.state.has_fix is True
        assert m.state.latitude_deg is not None

        m._process_line(self._NO_FIX_GGA)  # noqa: SLF001
        assert m.state.has_fix is False
        assert m.state.latitude_deg is None
        assert m.state.longitude_deg is None
        assert m.state.altitude_m is None

    def test_gga_fix_loss_still_updates_satellites_and_hdop(self) -> None:
        # satellite_anomaly/hdop_anomaly do not gate on has_fix - they
        # must see the *current* (degraded) reading, not a frozen
        # pre-loss value, or the analysis engine loses exactly the
        # signal that would explain why the fix was lost.
        m = self._monitor()
        m._process_line(self._VALID_GGA)  # noqa: SLF001
        assert m.state.num_satellites == 8
        assert m.state.hdop == pytest.approx(0.9)

        m._process_line(self._NO_FIX_GGA)  # noqa: SLF001
        assert m.state.num_satellites == 0
        assert m.state.hdop == pytest.approx(99.99)

    def test_rmc_void_clears_has_fix_position_and_speed(self) -> None:
        m = self._monitor()
        m._process_line(self._VALID_RMC)  # noqa: SLF001
        assert m.state.has_fix is True
        assert m.state.latitude_deg is not None

        m._process_line(self._VOID_RMC)  # noqa: SLF001
        assert m.state.has_fix is False
        assert m.state.latitude_deg is None
        assert m.state.longitude_deg is None
        assert m.state.speed_mps is None

    def test_fix_reacquisition_after_loss_sets_has_fix_true_again(self) -> None:
        m = self._monitor()
        m._process_line(self._VALID_GGA)  # noqa: SLF001
        m._process_line(self._NO_FIX_GGA)  # noqa: SLF001
        assert m.state.has_fix is False

        m._process_line(self._VALID_GGA)  # noqa: SLF001
        assert m.state.has_fix is True
        assert m.state.latitude_deg is not None

    def test_gsv_cn0_tracking_survives_fix_loss(self) -> None:
        # Independent of fix state entirely - see record_tracked_satellites.
        m = self._monitor()
        m._process_line(self._VALID_GGA)  # noqa: SLF001
        m._process_line(  # noqa: SLF001
            "$GPGSV,1,1,01,04,42,298,34*00"
        )
        m._process_line(self._NO_FIX_GGA)  # noqa: SLF001
        assert m.state.has_fix is False
        assert m.state.tracked_cn0_dbhz(time.monotonic()) == (34,)