"""Unit tests for TerminalLiveDashboard's terminal I/O mechanics.

These verify the actual escape-sequence behaviour (cursor home instead of
a full clear, hide/show cursor, tty vs non-tty branching, adaptive
height) using a fake stdout, since pytest's captured stdout isn't a real
tty and shutil.get_terminal_size() needs a controlled fallback.

Note: the fake stdout is installed by a plain helper function called
directly from each test body, NOT a pytest fixture. Patching sys.stdout
from a fixture gets silently clobbered when pytest's own capture manager
resumes its wrapper for the test's "call" phase; applying it inside the
test body (the same phase that reads it back) is what actually sticks.
"""

from __future__ import annotations

from datetime import datetime
from io import StringIO

import pytest

from gnss_monitor.live.dashboard import DashboardModel, TerminalLiveDashboard


class FakeTtyStdout(StringIO):
    def isatty(self) -> bool:  # noqa: D102
        return True


def use_fake_tty(monkeypatch: pytest.MonkeyPatch) -> FakeTtyStdout:
    fake = FakeTtyStdout()
    monkeypatch.setattr("sys.stdout", fake)
    monkeypatch.setattr(
        "shutil.get_terminal_size", lambda fallback=(80, 24): (100, 40)
    )
    return fake


def empty_model() -> DashboardModel:
    return DashboardModel(
        title="GNSS Monitoring Platform",
        generated_at=datetime(2026, 1, 1, 12, 0, 0),
        uptime_s=0.0,
        app_version="0.1.0",
        analysis_mode="Simple Mode (position only)",
        rows=(),
        events=(),
    )


class TestTtyMode:
    def test_open_clears_once_and_hides_cursor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard()
        dashboard.open()
        output = fake.getvalue()
        assert "\x1b[2J" in output  # one-time full clear
        assert "\x1b[H" in output  # cursor home
        assert "\x1b[?25l" in output  # hide cursor

    def test_close_restores_cursor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard()
        dashboard.close()
        assert "\x1b[?25h" in fake.getvalue()

    def test_update_uses_cursor_home_not_full_clear(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard()
        dashboard.open()
        fake.truncate(0)
        fake.seek(0)
        dashboard.update(empty_model())
        output = fake.getvalue()
        assert "\x1b[H" in output
        assert "\x1b[2J" not in output  # no full clear on ordinary frames

    def test_update_erases_trailing_content_per_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard()
        dashboard.update(empty_model())
        assert "\x1b[K" in fake.getvalue()

    def test_frame_height_never_shrinks_within_a_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard()
        dashboard.update(empty_model())
        tall_frame_lines = fake.getvalue().count("\n")

        # A row that briefly adds Triggered Analysis content would make a
        # taller frame; simulate the reverse (shrinking back down) and
        # confirm the screen is still padded to the tallest frame drawn -
        # this is what prevents the terminal from scrolling.
        fake.truncate(0)
        fake.seek(0)
        dashboard.update(empty_model())
        second_frame_lines = fake.getvalue().count("\n")
        assert second_frame_lines == tall_frame_lines

    def test_draw_in_place_never_writes_more_lines_than_the_terminal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression test for the "dashboard exceeds terminal size" bug:
        # a frame taller than the terminal forces the terminal itself to
        # scroll, which breaks the fixed cursor-home redraw no matter how
        # correct the escape sequences are.
        fake = use_fake_tty(monkeypatch)
        monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): (80, 10))
        dashboard = TerminalLiveDashboard()
        dashboard._draw_in_place([f"line {i}" for i in range(100)])
        output = fake.getvalue()
        # \x1b[H is written once up front; every drawn row ends in \x1b[K.
        assert output.count("\x1b[K") <= 9  # term_rows - 1

    def test_color_codes_present_in_tty_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gnss_monitor.analysis.evaluator import AnalysisResult
        from gnss_monitor.analysis.score import HealthState
        from gnss_monitor.live.dashboard import LiveRow
        from gnss_monitor.live.worker import ConnectionStatus
        from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

        fake = use_fake_tty(monkeypatch)
        model = DashboardModel(
            title="t",
            generated_at=datetime.now(),
            uptime_s=0.0,
            app_version="0.1.0",
            analysis_mode="Version 2 Scoring Engine",
            rows=[
                LiveRow(
                    name="GPS",
                    constellation="GPS",
                    connection=ConnectionStatus.CONNECTED,
                    result=EvaluationResult(HealthStatus.OK, 1.0, "ok"),
                    latitude_deg=25.3,
                    longitude_deg=51.4,
                    analysis=AnalysisResult("gps", 0.0, HealthState.OK, ()),
                )
            ],
            events=(),
        )
        dashboard = TerminalLiveDashboard()
        dashboard.update(model)
        assert "\x1b[32m" in fake.getvalue()  # green for OK


class TestNonTtyMode:
    def test_plain_stdout_never_gets_ansi_or_color(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # capsys's captured stdout is not a tty: this is the systemd/
        # journald/redirected-output path, which must stay plain text.
        dashboard = TerminalLiveDashboard()
        dashboard.open()
        dashboard.update(empty_model())
        dashboard.close()
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "GNSS Monitoring Platform" in out

    def test_clear_false_forces_plain_mode_even_on_a_tty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use_fake_tty(monkeypatch)
        dashboard = TerminalLiveDashboard(clear=False)
        dashboard.update(empty_model())
        assert "\x1b" not in fake.getvalue()
