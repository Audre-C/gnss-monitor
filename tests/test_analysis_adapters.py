"""Unit tests for gnss_monitor.analysis.adapters."""

from __future__ import annotations

from gnss_monitor.analysis.adapters import receiver_sample_from_state
from gnss_monitor.model import FixQuality
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
