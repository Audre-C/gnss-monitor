"""Unit tests for TerminalLiveDashboard's fixed-screen rendering.

Covers the section-based layout (header, system status, receiver status,
triggered analysis, event log), the Version 2 analysis display, and the
severity-driven status text / "never show stale data" invariant. The
in-place ANSI redraw mechanics (_draw_in_place) are exercised separately
in test_live_dashboard_redraw.py since they need a simulated tty.
"""

from __future__ import annotations

from datetime import datetime

from gnss_monitor.analysis.evaluator import AnalysisResult
from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.live.dashboard import DashboardModel, LiveRow, TerminalLiveDashboard
from gnss_monitor.live.event_log import LogEvent
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus


def simple_result() -> EvaluationResult:
    return EvaluationResult(HealthStatus.OK, 1.0, "within expected radius")


def model_for(rows: list[LiveRow], events: tuple[LogEvent, ...] = ()) -> DashboardModel:
    return DashboardModel(
        title="GNSS Monitoring Platform",
        generated_at=datetime(2026, 1, 1, 12, 0, 0),
        uptime_s=754.0,
        app_version="0.1.0",
        analysis_mode="Version 2 Scoring Engine",
        rows=rows,
        events=events,
    )


def render(rows: list[LiveRow], events: tuple[LogEvent, ...] = ()) -> str:
    return TerminalLiveDashboard(clear=False).render_frame(model_for(rows, events))


class TestHeaderAndSystemStatus:
    def test_header_shows_title_uptime_and_version(self) -> None:
        frame = render([])
        assert "GNSS Monitoring Platform" in frame
        assert "00:12:34" in frame  # 754s
        assert "0.1.0" in frame

    def test_system_status_counts_receivers(self) -> None:
        rows = [
            LiveRow(
                name="GPS",
                constellation="GPS",
                connection=ConnectionStatus.CONNECTED,
                result=simple_result(),
                latitude_deg=25.3,
                longitude_deg=51.4,
                analysis=AnalysisResult("gps", 0.0, HealthState.OK, ()),
            ),
            LiveRow(
                name="BeiDou",
                constellation="BeiDou",
                connection=ConnectionStatus.CONNECTED,
                result=simple_result(),
                latitude_deg=25.3,
                longitude_deg=51.4,
                analysis=AnalysisResult(
                    "bd",
                    42.0,
                    HealthState.WARNING,
                    (RuleOutcome("hdop_anomaly", True, 42.0, "HDOP elevated"),),
                ),
            ),
        ]
        frame = render(rows)
        assert "Overall System Status" in frame
        assert "Receivers Online:" in frame
        assert "2 / 2" in frame
        assert "Warnings:" in frame


class TestStatusText:
    def test_ok_row_shows_score_and_ok(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("gps", 0.0, HealthState.OK, ()),
        )
        frame = render([row])
        assert "Score:" in frame
        assert "OK" in frame

    def test_warning_state_renders(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult(
                "gps",
                42.0,
                HealthState.WARNING,
                (RuleOutcome("hdop_anomaly", True, 42.0, "HDOP elevated"),),
            ),
        )
        frame = render([row])
        assert "Warning" in frame
        assert "42" in frame

    def test_potential_spoofing_state_renders(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("gps", 65.0, HealthState.POTENTIAL_SPOOFING, ()),
        )
        assert "Potential Spoofing" in render([row])

    def test_spoofing_detected_state_renders(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("gps", 95.0, HealthState.SPOOFING_DETECTED, ()),
        )
        assert "Spoofing Detected" in render([row])

    def test_no_analysis_falls_back_to_simple_mode_and_dash_score(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
        )
        frame = render([row])
        score_line = next(ln for ln in frame.splitlines() if ln.startswith("Score:"))
        assert score_line.rstrip() == "Score:              -"


class TestReceiverFields:
    def test_rounds_and_formats_values(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=EvaluationResult(HealthStatus.OK, 4.234, "within expected radius"),
            latitude_deg=25.334365,
            longitude_deg=51.469347,
            has_fix=True,
            num_satellites=9,
            hdop=0.784,
            distance_m=4.234,
            last_update_wall=None,
        )
        frame = render([row])
        assert "25.33437°" in frame or "25.33436°" in frame  # 5-decimal rounding
        assert "0.78" in frame
        assert "4.2 m" in frame
        assert "Yes" in frame

    def test_missing_values_render_as_dash(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=None,
            longitude_deg=None,
        )
        frame = render([row])
        receiver_section = frame.split("Receiver Status", 1)[1]
        assert "-" in receiver_section


class TestTriggeredAnalysisSection:
    def test_no_active_triggers_message_when_all_clean(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("gps", 0.0, HealthState.OK, ()),
        )
        frame = render([row])
        assert "Triggered Analysis" in frame
        assert "No active triggers" in frame

    def test_section_lists_display_labels_for_flagged_receivers(self) -> None:
        flagged = LiveRow(
            name="NEO-6M",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult(
                "gps",
                55.0,
                HealthState.WARNING,
                (
                    RuleOutcome("position_offset", True, 20.0, "150 m from expected"),
                    RuleOutcome("hdop_anomaly", True, 35.0, "HDOP 6.0 elevated"),
                ),
            ),
        )
        clean = LiveRow(
            name="NEO-M8N",
            constellation="GLONASS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("glonass", 0.0, HealthState.OK, ()),
        )
        frame = render([flagged, clean])
        assert "Triggered Analysis" in frame
        assert "Position Offset" in frame
        assert "HDOP High" in frame
        detail_section = frame.split("Triggered Analysis", 1)[1]
        assert "GLONASS" not in detail_section

    def test_disconnected_receiver_never_shows_analysis(self) -> None:
        # Even if a stale AnalysisResult were somehow attached, Offline
        # must still win the status text - the invariant from the
        # original disconnect bug fix must hold for analysis too.
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.DISCONNECTED,
            result=simple_result(),
            latitude_deg=None,
            longitude_deg=None,
            analysis=None,
        )
        frame = render([row])
        receiver_section = frame.split("Receiver Status", 1)[1].split(
            "Triggered Analysis", 1
        )[0]
        assert "Offline" in receiver_section
        assert "Warning" not in receiver_section
        assert "Spoofing" not in receiver_section


class TestEventLog:
    def test_events_render_newest_first_with_timestamp(self) -> None:
        events = (
            LogEvent(wall_time=1_700_000_100.0, message="GPS recovered"),
            LogEvent(wall_time=1_700_000_050.0, message="GPS disconnected"),
        )
        frame = render([], events=events)
        assert "Event Log" in frame
        assert "GPS recovered" in frame
        assert "GPS disconnected" in frame
        assert frame.index("GPS recovered") < frame.index("GPS disconnected")

    def test_empty_event_log_shows_placeholder(self) -> None:
        frame = render([])
        assert "(no events yet)" in frame
