"""End-to-end live-controller test using the threaded path over files.

Runs the full concurrent pipeline (worker threads -> framer -> parser ->
evaluator) against the real test_data corpus by pointing file sources
through the live controller. This exercises the threading, snapshotting,
constellation labelling, and evaluation without requiring serial hardware.
"""

from __future__ import annotations

import time
from typing import Optional

from gnss_monitor.analysis.score import HealthState
from gnss_monitor.config.schema import (
    AnalysisConfig,
    AppSection,
    ChannelConfig,
    DisagreementScoringConfig,
    ExpectedBaseline,
    FileSourceConfig,
    HdopScoringConfig,
    NoFixScoringConfig,
    OverallThresholdsConfig,
    PositionScoringConfig,
    RootConfig,
    SatellitesScoringConfig,
    SerialSourceConfig,
    SiteSection,
    SpeedScoringConfig,
    TimeScoringConfig,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor import HealthStatus
from gnss_monitor.sources.base import NMEASource
from tests.fixtures import dataset_path


def analysis_config() -> AnalysisConfig:
    return AnalysisConfig(
        position=PositionScoringConfig(
            warning_radius_m=100.0,
            failure_radius_m=500.0,
            weight_warning=20.0,
            weight_failure=40.0,
        ),
        no_fix=NoFixScoringConfig(weight=30.0),
        speed=SpeedScoringConfig(
            stationary_speed_limit_kmh=5.0, warning_speed_kmh=10.0, weight=25.0
        ),
        satellites=SatellitesScoringConfig(minimum=5, sudden_drop=3, weight=15.0),
        hdop=HdopScoringConfig(warning=2.5, critical=5.0, weight=20.0),
        time=TimeScoringConfig(max_jump_seconds=3.0, weight=30.0),
        disagreement=DisagreementScoringConfig(
            max_distance_between_receivers_m=100.0, weight=35.0
        ),
        overall=OverallThresholdsConfig(
            warning=30.0, potential_spoofing=60.0, spoofing=90.0
        ),
    )


def build_config(
    radius_m: float = 100.0,
    analysis: Optional[AnalysisConfig] = None,
) -> RootConfig:
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
        analysis=analysis,
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
        model = controller.dashboard_model()
    finally:
        controller.stop()

    frame = TerminalLiveDashboard(clear=False).render_frame(model)
    assert "GNSS Monitoring Platform" in frame
    assert "Health:" in frame
    assert "Receiver Status" in frame
    assert "Lat" in frame
    assert "Lon" in frame
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


def test_no_analysis_config_means_no_analysis_results() -> None:
    # No `analysis:` section -> Version 2 scoring never runs; rows and the
    # dedicated accessor both reflect that explicitly rather than silently
    # returning zeros.
    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard(), refresh_interval_s=0.05
    )
    controller.start()
    try:
        controller.wait_until_sources_exhausted(timeout_s=5.0)
        rows = controller.rows()
        analysis_results = controller.analysis_results()
    finally:
        controller.stop()

    assert analysis_results == {}
    assert all(row.analysis is None for row in rows)


def test_healthy_receivers_are_ok_when_analysis_configured() -> None:
    controller = LiveController(
        build_config(analysis=analysis_config()),
        dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    controller.start()
    try:
        controller.wait_until_sources_exhausted(timeout_s=5.0)
        rows = controller.rows()
        analysis_results = controller.analysis_results()
    finally:
        controller.stop()

    assert set(analysis_results.keys()) == {"neo6m_1", "neom10_1", "lc29h_1"}
    for rid, result in analysis_results.items():
        # Real capture data isn't perfectly clean (e.g. neom10's fixture
        # has a low satellite count and elevated HDOP), so this only
        # asserts the *state*, not a specific score.
        assert result.state is HealthState.OK, (rid, result.triggered_rules)
    for row in rows:
        assert row.analysis is not None
        assert row.analysis.state is HealthState.OK


def test_silent_receiver_hides_analysis_result_too() -> None:
    # Extends the disconnect-bug regression test above: with analysis
    # configured, a receiver that goes stale must also stop reporting an
    # analysis score, for the same "never show stale data" reason it
    # already stops reporting a position.
    config = build_single_channel_config(receiver_timeout_s=0.15)
    config.analysis = analysis_config()
    controller = LiveController(
        config,
        dashboard=NullLiveDashboard(),
        source_factory=lambda _channel: SilentStallSource(),
        refresh_interval_s=0.02,
    )
    controller.start()
    try:
        assert wait_for(
            lambda: controller.rows()[0].analysis is not None
            and controller.rows()[0].analysis.state is HealthState.OK
        )
        assert wait_for(
            lambda: controller.results()["neo6m_1"].status
            is HealthStatus.NO_DATA,
            timeout=3.0,
        )
        row = next(r for r in controller.rows() if r.name == "NEO-6M")
        assert row.analysis is None
        assert controller.analysis_results() == {}
    finally:
        controller.stop()


def _gga_line(lat_deg: float, lon_deg: float) -> str:
    """Build a checksum-correct $GNGGA line for an arbitrary position."""
    lat_dir = "N" if lat_deg >= 0 else "S"
    lon_dir = "E" if lon_deg >= 0 else "W"
    lat_abs, lon_abs = abs(lat_deg), abs(lon_deg)
    lat_deg_part, lat_min = int(lat_abs), (lat_abs % 1) * 60
    lon_deg_part, lon_min = int(lon_abs), (lon_abs % 1) * 60
    body = (
        f"GNGGA,113954.00,{lat_deg_part:02d}{lat_min:08.5f},{lat_dir},"
        f"{lon_deg_part:03d}{lon_min:08.5f},{lon_dir},1,08,1.41,39.1,M,-24.7,M,,"
    )
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}"


class FixedPositionSource(NMEASource):
    """Emits one fixed-position GGA line, then goes idle (never exhausts)."""

    def __init__(self, lat_deg: float, lon_deg: float) -> None:
        self._line: Optional[str] = _gga_line(lat_deg, lon_deg)

    @property
    def source_id(self) -> str:
        return "fixed_position"

    def open(self) -> None:
        return None

    def read_line(self):
        line, self._line = self._line, None
        return line

    @property
    def is_exhausted(self) -> bool:
        return False

    def close(self) -> None:
        return None


def test_reference_role_excludes_receiver_from_disagreement() -> None:
    # Two channels: a dedicated constellation receiver and a reference
    # (multi-constellation) receiver placed ~1 km away. If role: reference
    # were not honoured, the constellation receiver would see the distant
    # reference as a "peer" and flag disagreement; because it's excluded,
    # the constellation receiver instead reports no peers to compare
    # against, proving ChannelConfig.role actually reaches the evaluator.
    baseline = ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=2000.0,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=0,
        max_hdop=50.0,
    )
    channels = [
        ChannelConfig(
            id="gps_1",
            module="u-blox NEO-6M",
            name="GPS",
            constellation="GPS",
            source=SerialSourceConfig(type="serial", port="COM_A", baud=9600),
        ),
        ChannelConfig(
            id="ref_1",
            module="Quectel LC29HEA",
            name="REF",
            role="reference",
            source=SerialSourceConfig(type="serial", port="COM_B", baud=9600),
        ),
    ]
    config = RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test Site", expected=baseline),
        channels=channels,
        analysis=analysis_config(),
    )
    sources = {
        "gps_1": FixedPositionSource(25.334373, 51.469359),
        "ref_1": FixedPositionSource(25.343373, 51.469359),  # ~1 km north
    }
    controller = LiveController(
        config,
        dashboard=NullLiveDashboard(),
        source_factory=lambda channel: sources[channel.id],
        refresh_interval_s=0.02,
    )
    controller.start()
    try:
        assert wait_for(lambda: controller.analysis_results().keys() == {"gps_1", "ref_1"})
        results = controller.analysis_results()
    finally:
        controller.stop()

    assert not any(o.name == "disagreement" for o in results["gps_1"].triggered_rules)
    assert not any(o.name == "disagreement" for o in results["ref_1"].triggered_rules)


def test_dashboard_model_reports_uptime_version_and_mode() -> None:
    controller = LiveController(
        build_config(analysis=analysis_config()),
        dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    controller.start()
    try:
        time.sleep(0.05)
        model = controller.dashboard_model()
    finally:
        controller.stop()

    assert model.title == "GNSS Monitoring Platform"
    assert model.uptime_s > 0.0
    assert model.app_version  # non-empty
    assert model.analysis_mode == "Version 2 Scoring Engine"
    assert len(model.rows) == 3


def test_dashboard_model_reports_simple_mode_when_analysis_absent() -> None:
    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard(), refresh_interval_s=0.05
    )
    controller.start()
    try:
        model = controller.dashboard_model()
    finally:
        controller.stop()
    assert model.analysis_mode == "Simple Mode (position only)"


def test_start_records_application_started_event() -> None:
    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard(), refresh_interval_s=0.05
    )
    controller.start()
    try:
        messages = [e.message for e in controller.dashboard_model().events]
    finally:
        controller.stop()
    assert "Application started" in messages


def _snapshot(
    receiver_id: str,
    name: str,
    connection: ConnectionStatus,
    has_fix: bool,
) -> "ReceiverSnapshot":
    from gnss_monitor.live.worker import ReceiverSnapshot
    from gnss_monitor.monitor.receiver_monitor import ReceiverState

    return ReceiverSnapshot(
        receiver_id=receiver_id,
        name=name,
        port="COM_TEST",
        connection=connection,
        state=ReceiverState(has_fix=has_fix, sentences_seen=1),
        last_update_wall=time.time(),
        source_exhausted=False,
    )


def test_event_log_records_connection_and_fix_transitions() -> None:
    # Drive the private transition-tracking methods directly with
    # synthetic snapshots: this is the same logic the real threaded path
    # uses, but deterministic - going through real worker threads makes
    # the "first observation" (which only seeds a baseline, see
    # _record_fix_event) and the "later transition" race unpredictably.
    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard()
    )
    receiver_id, name = "neo6m_1", "NEO-6M"  # constellation "GPS" per build_config

    # Baseline: connecting, no fix yet.
    controller._record_connection_event(  # noqa: SLF001 - exercising internals
        _snapshot(receiver_id, name, ConnectionStatus.CONNECTING, has_fix=False)
    )
    controller._record_fix_event(  # noqa: SLF001
        _snapshot(receiver_id, name, ConnectionStatus.CONNECTING, has_fix=False)
    )
    # Read the event log directly rather than via dashboard_model(): that
    # also runs rows()/_evaluations() over the controller's real (never
    # started) workers, which would re-observe "neo6m_1" itself and
    # contaminate the transition history this test is building by hand.
    assert controller._event_log.recent() == []  # noqa: SLF001

    # Connects and acquires a fix.
    controller._record_connection_event(  # noqa: SLF001
        _snapshot(receiver_id, name, ConnectionStatus.CONNECTED, has_fix=False)
    )
    controller._record_fix_event(  # noqa: SLF001
        _snapshot(receiver_id, name, ConnectionStatus.CONNECTED, has_fix=True)
    )
    messages = [e.message for e in controller._event_log.recent()]  # noqa: SLF001
    assert "GPS connected" in messages
    assert "GPS fix acquired" in messages

    # Disconnects.
    controller._record_connection_event(  # noqa: SLF001
        _snapshot(receiver_id, name, ConnectionStatus.DISCONNECTED, has_fix=True)
    )
    messages = [e.message for e in controller._event_log.recent()]  # noqa: SLF001
    assert "GPS disconnected" in messages

    # Reconnects: this is "recovered", not "connected" again.
    controller._record_connection_event(  # noqa: SLF001
        _snapshot(receiver_id, name, ConnectionStatus.CONNECTED, has_fix=True)
    )
    messages = [e.message for e in controller._event_log.recent()]  # noqa: SLF001
    assert "GPS recovered" in messages