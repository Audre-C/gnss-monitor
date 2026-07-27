"""Unit tests for TerminalLiveDashboard's Version 2 analysis rendering.

Simple Mode-only rendering (no `analysis` on any row) is already covered
by test_live_controller.py / test_five_receivers.py; these tests focus
specifically on what changes when LiveRow.analysis is populated.
"""

from __future__ import annotations

from gnss_monitor.analysis.evaluator import AnalysisResult
from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.live.dashboard import LiveRow, TerminalLiveDashboard
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus


def baseline() -> ExpectedBaseline:
    return ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=100.0,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=4,
        max_hdop=5.0,
    )


def simple_result() -> EvaluationResult:
    return EvaluationResult(HealthStatus.OK, 1.0, "within expected radius")


def render(rows: list[LiveRow]) -> str:
    return TerminalLiveDashboard(clear=False).render_frame(baseline(), rows)


class TestScoreColumnAndStatus:
    def test_ok_row_shows_score_zero(self) -> None:
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
        assert "Score" in frame
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
        assert "WARN" in frame
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
        assert "POT-SPF" in render([row])

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
        assert "SPOOFED" in render([row])

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
        lines = [ln for ln in frame.splitlines() if ln.startswith("GPS")]
        assert len(lines) == 1
        assert "-" in lines[0]  # score column shows '-' when unset


class TestTriggeredRulesSection:
    def test_no_section_when_nothing_triggered(self) -> None:
        row = LiveRow(
            name="GPS",
            constellation="GPS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("gps", 0.0, HealthState.OK, ()),
        )
        assert "Triggered Rules" not in render([row])

    def test_section_lists_reasons_for_flagged_receivers(self) -> None:
        flagged = LiveRow(
            name="GPS",
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
            name="GLONASS",
            constellation="GLONASS",
            connection=ConnectionStatus.CONNECTED,
            result=simple_result(),
            latitude_deg=25.3,
            longitude_deg=51.4,
            analysis=AnalysisResult("glonass", 0.0, HealthState.OK, ()),
        )
        frame = render([flagged, clean])
        assert "Triggered Rules" in frame
        assert "150 m from expected" in frame
        assert "HDOP 6.0 elevated" in frame
        # The clean receiver has nothing to report, so its name must not
        # appear again after "Triggered Rules" even though it's in the
        # main table above that point.
        detail_section = frame.split("Triggered Rules", 1)[1]
        assert "GLONASS" not in detail_section

    def test_disconnected_receiver_never_shows_analysis(self) -> None:
        # Even if a stale AnalysisResult were somehow attached, NO_DATA /
        # DISCONNECTED must still win the status cell - the invariant
        # from the original disconnect bug fix must hold for analysis too.
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
        assert "OFFLINE" in frame
        assert "WARN" not in frame
        assert "SPOOFED" not in frame
