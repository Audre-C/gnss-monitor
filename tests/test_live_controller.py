"""End-to-end live-controller test using the threaded path over files.

Runs the full concurrent pipeline (worker threads -> framer -> parser ->
evaluator) against the real test_data corpus by pointing file sources
through the live controller. This exercises the threading, snapshotting,
constellation labelling, and evaluation without requiring serial hardware.
"""

from __future__ import annotations

import time

from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    FileSourceConfig,
    RootConfig,
    SerialSourceConfig,
    SiteSection,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor import HealthStatus
from gnss_monitor.sources.base import NMEASource
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


VALID_GGA = (
    "$GNGGA,113954.00,2520.06189,N,05128.16076,E,1,08,1.41,"
    "39.1,M,-24.7,M,,*59"
)


class SilentStallSource(NMEASource):
    """Emits one fix, then goes silent forever without raising or ever
    reporting exhausted.

    This reproduces the real-world failure mode where a USB-serial
    adapter is unplugged but the OS/driver never surfaces an I/O error:
    read_line() just stops returning anything, exactly like an idle-but-
    connected live serial port. The regression this guards against is
    the receiver being reported HEALTHY forever on its last fix.
    """

    def __init__(self) -> None:
        self._emitted = False

    @property
    def source_id(self) -> str:
        return "silent_stall"

    def open(self) -> None:
        return None

    def read_line(self):
        if not self._emitted:
            self._emitted = True
            return VALID_GGA
        return None

    @property
    def is_exhausted(self) -> bool:
        return False

    def close(self) -> None:
        return None


def build_single_channel_config(receiver_timeout_s: float) -> RootConfig:
    baseline = ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=100.0,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=receiver_timeout_s,
        min_satellites=4,
        max_hdop=5.0,
    )
    channel = ChannelConfig(
        id="neo6m_1",
        module="u-blox NEO-6M",
        name="NEO-6M",
        constellation="GPS",
        source=SerialSourceConfig(type="serial", port="COM_TEST", baud=9600),
    )
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test Site", expected=baseline),
        channels=[channel],
    )


def wait_for(predicate, timeout=3.0, interval=0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_silent_receiver_goes_no_data_and_hides_stale_position() -> None:
    # A receiver that stops sending bytes (without the port erroring out)
    # must age out to NO_DATA and stop showing its last-known position,
    # instead of being reported HEALTHY forever. Regression test for the
    # "stale sentence reused forever" bug.
    config = build_single_channel_config(receiver_timeout_s=0.15)
    controller = LiveController(
        config,
        dashboard=NullLiveDashboard(),
        source_factory=lambda _channel: SilentStallSource(),
        refresh_interval_s=0.02,
    )
    controller.start()
    try:
        # First it reports the fix normally, like a healthy receiver.
        assert wait_for(
            lambda: controller.results()["neo6m_1"].status
            is HealthStatus.OK
        )
        row = next(r for r in controller.rows() if r.name == "NEO-6M")
        assert row.latitude_deg is not None

        # The source never raises, so the connection stays CONNECTED -
        # this is purely the staleness timeout kicking in.
        assert wait_for(
            lambda: controller.results()["neo6m_1"].status
            is HealthStatus.NO_DATA,
            timeout=3.0,
        ), "receiver should time out to NO_DATA once data stops arriving"

        row = next(r for r in controller.rows() if r.name == "NEO-6M")
        assert row.connection is ConnectionStatus.CONNECTED
        assert row.result.status is HealthStatus.NO_DATA
        assert row.latitude_deg is None
        assert row.longitude_deg is None
    finally:
        controller.stop()