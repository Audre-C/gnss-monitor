"""Tests that the dashboard frame always fits inside the terminal.

This is the fix for the "dashboard exceeds terminal size" bug: a frame
taller or wider than the terminal is exactly what forces a terminal to
scroll out from under a fixed cursor-home redraw, no matter how careful
the escape-sequence mechanics are (see test_live_dashboard_redraw.py for
those). Every test here renders a frame at a specific terminal size and
asserts the result actually fits, across several receiver counts and
several terminal sizes - including the "standard 80x24 SSH terminal"
called out explicitly in the bug report.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from gnss_monitor.analysis.evaluator import AnalysisResult
from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.live.dashboard import DashboardModel, LiveRow, TerminalLiveDashboard
from gnss_monitor.live.event_log import LogEvent
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_RECEIVERS = [
    ("NEO-6M", "GPS"),
    ("VOLLGO", "BeiDou"),
    ("NeoM10", "Galileo"),
    ("NEO-M8N", "GLONASS"),
    ("LC29HEA", "Reference"),
]


def _stress_model(num_events: int = 20) -> DashboardModel:
    """Five receivers, every field populated, one flagged with multiple
    triggered rules - deliberately the tallest/widest realistic frame."""
    rows = []
    for i, (name, constellation) in enumerate(_RECEIVERS):
        triggered = (
            (
                RuleOutcome("position_offset", True, 20.0, "150 m from expected"),
                RuleOutcome("hdop_anomaly", True, 35.0, "HDOP 6.0 elevated"),
                RuleOutcome("satellite_anomaly", True, 10.0, "only 3 satellites"),
            )
            if i == 0
            else ()
        )
        rows.append(
            LiveRow(
                name=name,
                constellation=constellation,
                connection=ConnectionStatus.CONNECTED,
                result=EvaluationResult(HealthStatus.OK, 4.234, "within expected radius"),
                latitude_deg=25.334365,
                longitude_deg=51.469347,
                analysis=AnalysisResult(
                    name, 55.0 if i == 0 else 0.0, HealthState.WARNING if i == 0 else HealthState.OK, triggered
                ),
                has_fix=True,
                num_satellites=9,
                hdop=0.784,
                distance_m=4.234,
                last_update_wall=1_700_000_000.0,
            )
        )
    events = tuple(
        LogEvent(wall_time=1_700_000_000.0 + i, message=f"Some receiver event number {i}")
        for i in range(num_events)
    )
    return DashboardModel(
        title="GNSS Monitoring Platform",
        generated_at=datetime(2026, 1, 1, 12, 0, 0),
        uptime_s=754.0,
        app_version="0.1.0",
        analysis_mode="Version 2 Scoring Engine",
        rows=rows,
        events=events,
    )


def _render_at(monkeypatch: pytest.MonkeyPatch, cols: int, rows: int, model: DashboardModel) -> str:
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): (cols, rows))
    return TerminalLiveDashboard(clear=False).render_frame(model)


@pytest.mark.parametrize(
    ("cols", "rows"),
    [
        (80, 24),  # the standard SSH terminal named in the bug report
        (100, 40),
        (60, 20),
        (40, 15),
        (120, 50),
    ],
)
def test_frame_never_exceeds_terminal_bounds(
    monkeypatch: pytest.MonkeyPatch, cols: int, rows: int
) -> None:
    frame = _render_at(monkeypatch, cols, rows, _stress_model())
    lines = frame.splitlines()
    assert len(lines) <= rows - 1, f"{len(lines)} lines drawn on a {rows}-row terminal"
    too_wide = [ln for ln in lines if len(ln) > cols - 1]
    assert too_wide == [], f"lines exceeding {cols - 1} columns: {too_wide!r}"


def test_receiver_table_always_shows_every_receiver_even_at_80x24(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _render_at(monkeypatch, 80, 24, _stress_model())
    for name, constellation in _RECEIVERS:
        assert name in frame
        assert constellation in frame


def test_receiver_table_survives_a_short_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # 20 rows is enough for header + status + the full 5-row table but
    # nothing else - Triggered Analysis and the Event Log must vanish,
    # while every receiver stays listed (the one section required to
    # "always remain visible").
    frame = _render_at(monkeypatch, 80, 20, _stress_model())
    lines = frame.splitlines()
    assert len(lines) <= 19
    for name, _constellation in _RECEIVERS:
        assert name in frame
    assert "Event Log" not in frame


def test_receiver_table_shows_a_partial_hidden_notice_when_truly_cramped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Below the floor where even one receiver row plus the header/status
    # chrome fits, the table degrades to "as many rows as fit + a notice"
    # rather than let the frame exceed the terminal - the hard "never
    # exceed terminal height" constraint outranks "always show every
    # receiver" when the two are physically impossible to satisfy at once.
    frame = _render_at(monkeypatch, 80, 16, _stress_model())
    lines = frame.splitlines()
    assert len(lines) <= 15
    assert "NEO-6M" in frame
    assert "more receiver" in frame


def test_triggered_analysis_and_event_log_collapse_before_the_table_shrinks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tall_frame = _render_at(monkeypatch, 80, 60, _stress_model())
    short_frame = _render_at(monkeypatch, 80, 20, _stress_model())
    assert "Triggered Analysis" in tall_frame
    assert "Event Log" in tall_frame
    # Under height pressure the lower-priority sections disappear first,
    # never the receiver table itself.
    assert "Triggered Analysis" not in short_frame
    assert "Event Log" not in short_frame
    for name, _constellation in _RECEIVERS:
        assert name in short_frame


def _table_header_line(lines: list[str]) -> str:
    # The section title line is literally the text "Receiver Status",
    # which would also match a naive "Receiver" and "Status" substring
    # search - the real column header additionally contains "Score".
    return next(ln for ln in lines if "Receiver" in ln and "Score" in ln)


def test_core_columns_survive_a_narrow_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _render_at(monkeypatch, 45, 40, _stress_model())
    lines = frame.splitlines()
    assert all(len(ln) <= 44 for ln in lines)
    # Receiver/Status/Score/Lat/Lon are the non-negotiable core columns -
    # this app is useless without a position or a score.
    header_line = _table_header_line(lines)
    for column in ("Status", "Score", "Lat", "Lon"):
        assert column in header_line


def test_wide_terminal_keeps_every_column(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = _render_at(monkeypatch, 160, 50, _stress_model())
    lines = frame.splitlines()
    header_line = _table_header_line(lines)
    for column in ("Status", "Score", "Lat", "Lon", "Fix", "Sat", "HDOP", "Dist", "Age"):
        assert column in header_line


def test_event_log_message_is_truncated_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    model = DashboardModel(
        title="GNSS Monitoring Platform",
        generated_at=datetime(2026, 1, 1, 12, 0, 0),
        uptime_s=0.0,
        app_version="0.1.0",
        analysis_mode="Simple Mode (position only)",
        rows=(),
        events=(
            LogEvent(
                wall_time=1_700_000_000.0,
                message="A" * 200,
            ),
        ),
    )
    frame = _render_at(monkeypatch, 80, 24, model)
    lines = frame.splitlines()
    assert all(len(ln) <= 79 for ln in lines)
    assert "…" in frame


def test_debug_flag_shows_terminal_size_in_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): (100, 30))
    model = _stress_model(num_events=0)
    dashboard = TerminalLiveDashboard(clear=False, debug=True)
    frame = dashboard.render_frame(model)
    assert "Terminal: 100x30" in frame


def test_debug_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): (100, 30))
    frame = TerminalLiveDashboard(clear=False).render_frame(_stress_model(num_events=0))
    assert "Terminal:" not in frame
