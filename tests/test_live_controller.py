"""End-to-end live-controller test using the threaded path over files.

Runs the full concurrent pipeline (worker threads -> framer -> parser ->
evaluator) against the real test_data corpus by pointing file sources
through the live controller. This exercises the threading, snapshotting,
constellation labelling, and evaluation without requiring serial hardware.
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import Optional

from gnss_monitor.analysis.score import HealthState
from gnss_monitor.config.schema import (
    AnalysisConfig,
    AppSection,
    ChannelConfig,
    DataLoggingConfig,
    DisagreementScoringConfig,
    ExpectedBaseline,
    FileSourceConfig,
    HdopScoringConfig,
    NoFixScoringConfig,
    OverallThresholdsConfig,
    ParsedLoggerConfig,
    PositionScoringConfig,
    RawNmeaLoggerConfig,
    RootConfig,
    SatellitesScoringConfig,
    SerialSourceConfig,
    SiteSection,
    SnapshotLoggerConfig,
    SpeedScoringConfig,
    TimeScoringConfig,
)
from gnss_monitor.geo import haversine_m
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor import EvaluationResult, HealthStatus
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
    data_logging: Optional[DataLoggingConfig] = None,
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
        data_logging=data_logging,
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


def test_rows_expose_avg_cn0_from_real_gsv_data() -> None:
    # The corpus fixtures contain real GSV sentences with populated SNR
    # fields, so draining them through the full threaded path must
    # populate LiveRow.avg_cn0_dbhz, not just leave it at the default.
    controller = LiveController(
        build_config(), dashboard=NullLiveDashboard(), refresh_interval_s=0.05
    )
    controller.start()
    try:
        controller.wait_until_sources_exhausted(timeout_s=5.0)
        rows = controller.rows()
    finally:
        controller.stop()

    by_name = {r.name: r for r in rows}
    assert by_name["NEO-6M"].avg_cn0_dbhz is not None
    assert by_name["NEO-6M"].avg_cn0_dbhz > 0.0


def _full_snapshot(
    receiver_id: str,
    name: str,
    port: str = "COM_TEST",
    connection: ConnectionStatus = ConnectionStatus.CONNECTED,
    **state_kwargs,
) -> "ReceiverSnapshot":
    from gnss_monitor.live.worker import ReceiverSnapshot
    from gnss_monitor.monitor.receiver_monitor import ReceiverState

    return ReceiverSnapshot(
        receiver_id=receiver_id,
        name=name,
        port=port,
        connection=connection,
        state=ReceiverState(**state_kwargs),
        last_update_wall=time.time(),
        source_exhausted=False,
    )


class TestStatusChangeDetail:
    """_evaluate()'s status-changed log/event text must name the actual
    measurement that explains the transition, not just the two states -
    see LiveController._describe_status_transition."""

    def test_position_transition_logs_distance(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="gnss_monitor.live")
        controller = LiveController(
            build_config(radius_m=100.0), dashboard=NullLiveDashboard()
        )
        ok_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=True,
            sentences_seen=1,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            last_seen_monotonic=time.monotonic(),
        )
        controller._evaluate(ok_snap)  # noqa: SLF001 - seeds the baseline, no log yet

        far_lat = 25.334373 + 0.01  # well beyond the 100 m radius
        expected_distance = haversine_m(
            25.334373, 51.469359, far_lat, 51.469359
        )
        failed_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=True,
            sentences_seen=2,
            latitude_deg=far_lat,
            longitude_deg=51.469359,
            last_seen_monotonic=time.monotonic(),
        )
        caplog.clear()
        controller._evaluate(failed_snap)  # noqa: SLF001

        assert "status changed: OK -> FAILED" in caplog.text
        assert f"{expected_distance:.0f} m" in caplog.text
        assert "0 m ->" in caplog.text  # the OK snapshot's distance was ~0

        messages = [e.message for e in controller._event_log.recent()]  # noqa: SLF001
        assert any("FAILED" in m and "distance" in m for m in messages)

    def test_no_fix_transition_logs_satellites_and_hdop(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="gnss_monitor.live")
        controller = LiveController(build_config(), dashboard=NullLiveDashboard())
        ok_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=True,
            sentences_seen=1,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            num_satellites=8,
            hdop=0.9,
            last_seen_monotonic=time.monotonic(),
        )
        controller._evaluate(ok_snap)  # noqa: SLF001

        no_fix_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=False,
            sentences_seen=2,
            num_satellites=0,
            hdop=None,
            last_seen_monotonic=time.monotonic(),
        )
        caplog.clear()
        controller._evaluate(no_fix_snap)  # noqa: SLF001

        assert "status changed: OK -> NO FIX" in caplog.text
        assert "fix lost" in caplog.text
        assert "satellites 8 -> 0" in caplog.text
        assert "hdop 0.9 -> --" in caplog.text

    def test_timeout_transition_logs_last_sentence_and_port(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="gnss_monitor.live")
        controller = LiveController(build_config(), dashboard=NullLiveDashboard())
        ok_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            port="/dev/ttyCH9344USB4",
            has_fix=True,
            sentences_seen=1,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            last_seen_monotonic=time.monotonic(),
        )
        controller._evaluate(ok_snap)  # noqa: SLF001

        # build_config()'s baseline has receiver_timeout_s=10.0; 20s of
        # silence is well past it.
        stale_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            port="/dev/ttyCH9344USB4",
            has_fix=True,
            sentences_seen=5,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            last_seen_monotonic=time.monotonic() - 20.0,
        )
        caplog.clear()
        controller._evaluate(stale_snap)  # noqa: SLF001

        assert "status changed: OK -> NO DATA" in caplog.text
        assert "receiver timeout" in caplog.text
        assert "last sentence" in caplog.text
        assert "port /dev/ttyCH9344USB4" in caplog.text


class TestAnalysisStateChangeDetail:
    """_analysis_results()'s state-changed log/event text must include
    the previous and new score plus which rules are driving it, not just
    the two HealthState names - see Part 2 of the C/N0/logging work."""

    def test_state_change_logs_scores_and_triggered_rules(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="gnss_monitor.live")
        controller = LiveController(
            build_config(analysis=analysis_config()), dashboard=NullLiveDashboard()
        )
        dummy_result = EvaluationResult(HealthStatus.OK, 0.0, "n/a")

        healthy_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=True,
            sentences_seen=1,
            latitude_deg=25.334373,
            longitude_deg=51.469359,
            num_satellites=10,
            hdop=1.0,
            last_seen_monotonic=time.monotonic(),
        )
        controller._analysis_results(  # noqa: SLF001 - seeds the baseline, no log yet
            [(healthy_snap, dummy_result, True)]
        )

        # ~1112 m offset: past position.failure_radius_m (500 m), so
        # position_offset alone scores weight_failure (40) -> Warning.
        far_snap = _full_snapshot(
            "neo6m_1",
            "NEO-6M",
            has_fix=True,
            sentences_seen=2,
            latitude_deg=25.334373 + 0.01,
            longitude_deg=51.469359,
            num_satellites=10,
            hdop=1.0,
            last_seen_monotonic=time.monotonic(),
        )
        caplog.clear()
        controller._analysis_results(  # noqa: SLF001
            [(far_snap, dummy_result, True)]
        )

        assert "analysis state changed: OK (0) -> Warning (40)" in caplog.text
        assert "triggered:" in caplog.text
        assert "Position Offset +40" in caplog.text

        messages = [e.message for e in controller._event_log.recent()]  # noqa: SLF001
        assert any("OK(0)" in m and "Warning(40)" in m for m in messages)


class TestDataLoggingIntegration:
    """End-to-end: LiveController wired to a real DataLogger over the real
    fixture corpus. Proves the whole observer chain (ReceiverMonitor's
    sink calls -> DataLogger's queue/thread -> the writers) produces real
    files with real content, and that leaving data_logging unset changes
    nothing about a run."""

    def test_disabled_by_default_creates_no_data_directory(
        self, tmp_path: Path
    ) -> None:
        controller = LiveController(
            build_config(), dashboard=NullLiveDashboard(), refresh_interval_s=0.05,
        )
        assert controller._data_logger.enabled is False  # noqa: SLF001
        controller.start()
        try:
            controller.wait_until_sources_exhausted(timeout_s=5.0)
            controller.rows()
        finally:
            controller.stop()
        assert not (tmp_path / "data").exists()

    def test_enabled_writes_raw_parsed_and_snapshot_files(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        config = build_config(
            data_logging=DataLoggingConfig(
                enabled=True,
                raw_nmea=RawNmeaLoggerConfig(enabled=True),
                parsed=ParsedLoggerConfig(enabled=True),
                snapshot=SnapshotLoggerConfig(enabled=True, interval_s=0.01),
                directory=data_dir,
            )
        )
        controller = LiveController(
            config, dashboard=NullLiveDashboard(), refresh_interval_s=0.05,
        )
        controller.start()
        try:
            controller.wait_until_sources_exhausted(timeout_s=5.0)
            controller.rows()  # drives at least one parsed/snapshot tick
        finally:
            controller.stop()  # flushes and joins the data-logger thread

        raw_files = list(data_dir.rglob("neo6m_1.nmea"))
        assert len(raw_files) == 1
        raw_lines = raw_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(raw_lines) > 0
        assert raw_lines[0].split(",", 1)[1].startswith("$")

        parsed_files = list(data_dir.rglob("parsed.csv"))
        assert len(parsed_files) == 1
        with open(parsed_files[0], newline="", encoding="utf-8") as handle:
            parsed_rows = list(csv.reader(handle))
        assert parsed_rows[0][:3] == ["timestamp", "receiver", "sentence"]
        assert len(parsed_rows) > 1
        assert any(row[1] == "neo6m_1" for row in parsed_rows[1:])

        snapshot_files = list(data_dir.rglob("snapshot.csv"))
        assert len(snapshot_files) == 1
        with open(snapshot_files[0], newline="", encoding="utf-8") as handle:
            snapshot_rows = list(csv.reader(handle))
        assert snapshot_rows[0][:4] == [
            "timestamp", "receiver", "constellation", "health",
        ]
        assert len(snapshot_rows) > 1
        names = {row[1] for row in snapshot_rows[1:]}
        assert "NEO-6M" in names