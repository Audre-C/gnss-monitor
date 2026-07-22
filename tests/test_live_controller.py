"""End-to-end live-controller test using the threaded path over files.

Runs the full concurrent pipeline (worker threads -> framer -> parser ->
evaluator) against the real test_data corpus by pointing file sources
through the live controller. This exercises the threading, snapshotting,
constellation labelling, and evaluation without requiring serial hardware.
"""

from __future__ import annotations

from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    FileSourceConfig,
    RootConfig,
    SiteSection,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.monitor import HealthStatus
from tests.fixtures import dataset_path


def build_config(radius_m: float = 100.0) -> RootConfig:
    baseline = ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=radius_m,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=4,
        max_hdop=5.0,
    )
    channels = [
        ChannelConfig(
            id="neo6m_1",
            module="u-blox NEO-6M",
            name="NEO-6M",
            constellation="GPS",
            source=FileSourceConfig(
                type="file", path=dataset_path("neo6m", "normal")
            ),
        ),
        ChannelConfig(
            id="neom10_1",
            module="Quescan NeoM101612F",
            name="NeoM10",
            constellation="GLONASS",
            source=FileSourceConfig(
                type="file", path=dataset_path("neom10", "normal")
            ),
        ),
        ChannelConfig(
            id="lc29h_1",
            module="Quectel LC29HEA",
            name="LC29HEA",
            constellation="Galileo",
            source=FileSourceConfig(
                type="file", path=dataset_path("lc29hea", "normal")
            ),
        ),
    ]
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test Site", expected=baseline),
        channels=channels,
    )


def test_all_receivers_healthy_over_threaded_path() -> None:
    controller = LiveController(
        build_config(radius_m=100.0),
        dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    controller.start()
    try:
        assert controller.wait_until_sources_exhausted(timeout_s=5.0)
        results = controller.results()
    finally:
        controller.stop()

    assert set(results.keys()) == {"neo6m_1", "neom10_1", "lc29h_1"}
    for rid, result in results.items():
        assert result.status is HealthStatus.OK, (rid, result.reason)


def test_rows_expose_constellation_and_position() -> None:
    controller = LiveController(
        build_config(),
        dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    controller.start()
    try:
        controller.wait_until_sources_exhausted(timeout_s=5.0)
        rows = controller.rows()
    finally:
        controller.stop()

    by_name = {r.name: r for r in rows}
    assert by_name["NEO-6M"].constellation == "GPS"
    assert by_name["NeoM10"].constellation == "GLONASS"
    assert by_name["LC29HEA"].constellation == "Galileo"
    for row in rows:
        # Every fixture reaches a fix, so a position is populated.
        assert row.latitude_deg is not None
        assert row.longitude_deg is not None


def test_dashboard_frame_renders_new_layout() -> None:
    from gnss_monitor.live.dashboard import TerminalLiveDashboard

    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    controller.start()
    try:
        controller.wait_until_sources_exhausted(timeout_s=5.0)
        rows = controller.rows()
    finally:
        controller.stop()

    frame = TerminalLiveDashboard(clear=False).render_frame(
        build_config().site.expected, rows
    )
    assert "GNSS HEALTH MONITOR (LIVE)" in frame
    assert "GNSS" in frame
    assert "Latitude" in frame
    assert "Longitude" in frame
    assert "GPS" in frame
    assert "GLONASS" in frame
    assert "Galileo" in frame
    assert "NEO-6M" in frame