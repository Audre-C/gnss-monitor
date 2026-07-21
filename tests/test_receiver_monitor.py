"""Unit tests for ReceiverMonitor position extraction from real captures."""

from __future__ import annotations

from gnss_monitor.monitor import ReceiverMonitor
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