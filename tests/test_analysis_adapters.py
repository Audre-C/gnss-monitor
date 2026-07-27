"""Unit tests for gnss_monitor.analysis.adapters."""

from __future__ import annotations

import time

from gnss_monitor.analysis.adapters import receiver_sample_from_state
from gnss_monitor.model import FixQuality, SatelliteInfo
from gnss_monitor.monitor.receiver_monitor import ReceiverState


def test_translates_every_relevant_field() -> None:
    state = ReceiverState(
        has_fix=True,
        fix_quality=FixQuality.GPS,
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        num_satellites=9,
        hdop=1.2,
        speed_mps=0.3,
        last_fix_utc="113954.00",
    )
    sample = receiver_sample_from_state(state)
    assert sample.has_fix is True
    assert sample.latitude_deg == 25.334373
    assert sample.longitude_deg == 51.469359
    assert sample.num_satellites == 9
    assert sample.hdop == 1.2
    assert sample.speed_mps == 0.3
    assert sample.utc_time_s == 11 * 3600 + 39 * 60 + 54.0


def test_default_state_yields_no_fix_sample_with_no_data() -> None:
    sample = receiver_sample_from_state(ReceiverState())
    assert sample.has_fix is False
    assert sample.latitude_deg is None
    assert sample.num_satellites is None
    assert sample.hdop is None
    assert sample.speed_mps is None
    assert sample.utc_time_s is None
    assert sample.cn0_dbhz == ()


def test_cn0_dbhz_is_populated_from_tracked_satellites() -> None:
    state = ReceiverState()
    state.record_tracked_satellites(
        "GP",
        (
            SatelliteInfo(prn=4, elevation_deg=42, azimuth_deg=298, snr_dbhz=34),
            SatelliteInfo(prn=9, elevation_deg=14, azimuth_deg=275, snr_dbhz=31),
        ),
        t_rx_mono=time.monotonic(),
    )
    sample = receiver_sample_from_state(state)
    assert sample.cn0_dbhz is not None
    assert sorted(sample.cn0_dbhz) == [31, 34]
