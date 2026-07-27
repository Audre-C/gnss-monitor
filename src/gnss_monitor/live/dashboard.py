"""Fixed, in-place-redrawing terminal dashboard for live monitoring.

Earlier versions of this module printed one frame per tick and relied on
`clear`/`cls` to reset the screen - functional, but every redraw was a
full clear-then-repaint, which flickers and (worse, per the operators
actually watching this over SSH) can read as the terminal "scrolling"
depending on the emulator. This version instead:

    * moves the cursor back to the top of the screen (`ESC[H`) and
      overwrites each line in place, clearing only the trailing part of
      any line that got shorter (`ESC[K`) - never a full-screen clear
      after the first frame, which is what removes the flicker;
    * pads every frame up to the tallest frame drawn so far in this run,
      so a frame that's briefly shorter (e.g. "Triggered Analysis" empties
      out) can't leave stale text behind or cause the terminal to scroll;
    * only does any of this when stdout is an actual terminal. Piped or
      redirected output (systemd -> journald, `> file`, `| less`) falls
      back to plain, readable, append-only lines - exactly the behavior
      `journalctl -u gnss-monitor -f` already depends on.

The layout itself is a fixed ordered list of section-rendering functions
(_render_header, _render_system_status, ...), each independent and unit-
testable. Adding a future section (C/N0, constellation health, a
teleport comparison, ...) means writing one more such function and
adding it to that list - nothing here needs restructuring for that.
"""

from __future__ import annotations

import os
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Optional, Sequence

from gnss_monitor.analysis.evaluator import AnalysisResult
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.live.event_log import LogEvent
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 78
_BAR = "=" * _WIDTH
_RULE = "-" * _WIDTH
_LABEL_WIDTH = 20

_RESET = "\x1b[0m"
_COLOR_GREEN = "\x1b[32m"
_COLOR_YELLOW = "\x1b[33m"
_COLOR_ORANGE = "\x1b[1;33m"  # bold yellow: safe "orange" on 8-color terminals
_COLOR_RED = "\x1b[1;31m"
_COLOR_GRAY = "\x1b[90m"

_EVENT_LOG_CHROME_LINES = 4  # bar, title, bar, blank
_MIN_EVENT_LINES = 3

_RULE_LABELS = {
    "no_fix": "No GNSS Fix",
    "position_offset": "Position Offset",
    "sudden_speed": "Sudden Speed",
    "satellite_anomaly": "Low Satellites",
    "hdop_anomaly": "HDOP High",
    "time_discontinuity": "Time Discontinuity",
    "disagreement": "Cross-Receiver Disagreement",
}


@dataclass(frozen=True)
class LiveRow:
    """One receiver's data for one dashboard frame.

    has_fix/num_satellites/hdop/distance_m/last_update_wall are None
    both when analysis isn't configured for that field's source (Simple
    Mode never populates distance_m per se - it does, see below) and,
    more importantly, whenever the receiver's data isn't fresh
    (disconnected or stale) - see LiveController._evaluations(). "None"
    always means "not known right now", never "zero"/"no".
    """

    name: str
    constellation: str
    connection: ConnectionStatus
    result: EvaluationResult
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    analysis: Optional[AnalysisResult] = None
    has_fix: Optional[bool] = None
    num_satellites: Optional[int] = None
    hdop: Optional[float] = None
    distance_m: Optional[float] = None
    last_update_wall: Optional[float] = None


@dataclass(frozen=True)
class DashboardModel:
    """Everything one dashboard frame needs. The seam between "what
    LiveController knows" and "how it's displayed": a future section
    needs a new field here (with a default, so old callers still work)
    and one more _render_* function - never a change to this shape.
    """

    title: str
    generated_at: datetime
    uptime_s: float
    app_version: str
    analysis_mode: str
    rows: Sequence[LiveRow]
    events: Sequence[LogEvent]


class Severity(IntEnum):
    """Ascending display severity, used both per-row and for the
    aggregate "Overall Health" (the max severity across all rows)."""

    OK = 0
    PENDING = 1
    WARNING = 2
    OFFLINE = 3
    POTENTIAL_SPOOFING = 4
    SPOOFING_DETECTED = 5


_SEVERITY_LABELS = {
    Severity.OK: "OK",
    Severity.PENDING: "Pending",
    Severity.WARNING: "Warning",
    Severity.OFFLINE: "Offline",
    Severity.POTENTIAL_SPOOFING: "Potential Spoofing",
    Severity.SPOOFING_DETECTED: "Spoofing Detected",
}

_SEVERITY_COLOR = {
    Severity.OK: _COLOR_GREEN,
    Severity.PENDING: _COLOR_GRAY,
    Severity.WARNING: _COLOR_YELLOW,
    Severity.OFFLINE: _COLOR_GRAY,
    Severity.POTENTIAL_SPOOFING: _COLOR_ORANGE,
    Severity.SPOOFING_DETECTED: _COLOR_RED,
}

_ANALYSIS_STATE_SEVERITY = {
    HealthState.OK: Severity.OK,
    HealthState.WARNING: Severity.WARNING,
    HealthState.POTENTIAL_SPOOFING: Severity.POTENTIAL_SPOOFING,
    HealthState.SPOOFING_DETECTED: Severity.SPOOFING_DETECTED,
}


def _row_severity(row: LiveRow) -> Severity:
    if row.connection is ConnectionStatus.DISCONNECTED:
        return Severity.OFFLINE
    if row.connection is ConnectionStatus.CONNECTING:
        return Severity.PENDING
    if row.result.status is HealthStatus.NO_DATA:
        return Severity.PENDING
    if row.analysis is not None:
        return _ANALYSIS_STATE_SEVERITY[row.analysis.state]
    if row.result.status is HealthStatus.OK:
        return Severity.OK
    return Severity.WARNING  # Simple Mode FAILED (no fix / out of range)


def _row_status_text(row: LiveRow) -> str:
    severity = _row_severity(row)
    if severity is Severity.PENDING:
        text = "Connecting" if row.connection is ConnectionStatus.CONNECTING else "No Data"
        return text
    if severity is Severity.OFFLINE:
        return "Offline"
    if row.analysis is not None:
        return row.analysis.state.value
    if row.result.status is HealthStatus.OK:
        return "OK"
    return "No Fix" if row.result.distance_m is None else "Out Of Range"


def _overall_severity(rows: Sequence[LiveRow]) -> Severity:
    return max((_row_severity(row) for row in rows), default=Severity.OK)


def _colorize(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{_RESET}" if enabled and code else text


def _field(label: str, value: str, color: bool, width: int = _LABEL_WIDTH) -> str:
    # Pad the label BEFORE colorizing: colorizing after padding would
    # count the (invisible) escape codes toward the field width and
    # break column alignment the instant color is enabled.
    return _colorize(f"{label:<{width}}", _COLOR_GRAY, color) + value


def _rule_label(name: str) -> str:
    return _RULE_LABELS.get(name, name.replace("_", " ").title())


def _receiver_label(row: LiveRow) -> str:
    return row.name if row.constellation in (None, "-") else row.constellation


def _coord_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.5f}°"


def _int_text(value: Optional[int]) -> str:
    return "-" if value is None else str(value)


def _hdop_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}"


def _distance_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.1f} m"


def _score_text(row: LiveRow) -> str:
    return "-" if row.analysis is None else f"{row.analysis.score:.0f}"


def _fix_text(row: LiveRow) -> str:
    if row.has_fix is None:
        return "-"
    return "Yes" if row.has_fix else "No"


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.0f}m"


def _last_update_text(last_update_wall: Optional[float], now: datetime) -> str:
    if last_update_wall is None:
        return "-"
    updated_at = datetime.fromtimestamp(last_update_wall)
    age_s = max(0.0, (now - updated_at).total_seconds())
    return f"{updated_at:%H:%M:%S} ({_format_age(age_s)} ago)"


# ---------------------------------------------------------------------------
# Sections - each takes (model, color) and returns the lines it owns.
# ---------------------------------------------------------------------------


def _render_header(model: DashboardModel, color: bool) -> list[str]:
    return [
        _BAR,
        f" {model.title}",
        _BAR,
        "",
        _field("Time:", f"{model.generated_at:%Y-%m-%d %H:%M:%S}", color),
        _field("Uptime:", _format_duration(model.uptime_s), color),
        _field("Version:", model.app_version, color),
        "",
    ]


def _render_system_status(model: DashboardModel, color: bool) -> list[str]:
    rows = model.rows
    online = sum(1 for row in rows if row.connection is ConnectionStatus.CONNECTED)

    def _count(state: HealthState) -> int:
        return sum(
            1 for row in rows if row.analysis is not None and row.analysis.state is state
        )

    overall = _overall_severity(rows)
    overall_text = _colorize(
        _SEVERITY_LABELS[overall], _SEVERITY_COLOR[overall], color
    )
    return [
        _RULE,
        "Overall System Status",
        _RULE,
        "",
        _field("Overall Health:", overall_text, color),
        _field("Analysis Mode:", model.analysis_mode, color),
        _field("Receivers Online:", f"{online} / {len(rows)}", color),
        _field("Warnings:", str(_count(HealthState.WARNING)), color),
        _field(
            "Potential Spoofing:", str(_count(HealthState.POTENTIAL_SPOOFING)), color
        ),
        _field(
            "Spoofing Detected:", str(_count(HealthState.SPOOFING_DETECTED)), color
        ),
        "",
    ]


def _render_receiver_status(model: DashboardModel, color: bool) -> list[str]:
    lines = [_BAR, "Receiver Status", _BAR, ""]
    rows = model.rows
    for index, row in enumerate(rows):
        severity = _row_severity(row)
        status_text = _colorize(
            _row_status_text(row), _SEVERITY_COLOR[severity], color
        )
        if row.constellation not in (None, "-"):
            header = f"{row.constellation} ({row.name})"
        else:
            header = row.name
        lines.append(header)
        lines.append("")
        lines.append(_field("Status:", status_text, color))
        lines.append(_field("Score:", _score_text(row), color))
        lines.append(_field("Fix:", _fix_text(row), color))
        lines.append(_field("Latitude:", _coord_text(row.latitude_deg), color))
        lines.append(_field("Longitude:", _coord_text(row.longitude_deg), color))
        lines.append(_field("Satellites:", _int_text(row.num_satellites), color))
        lines.append(_field("HDOP:", _hdop_text(row.hdop), color))
        lines.append(_field("Distance:", _distance_text(row.distance_m), color))
        lines.append(
            _field(
                "Last Update:",
                _last_update_text(row.last_update_wall, model.generated_at),
                color,
            )
        )
        lines.append("")
        if index < len(rows) - 1:
            lines.append(_RULE)
            lines.append("")
    return lines


def _render_triggered_analysis(model: DashboardModel, color: bool) -> list[str]:
    lines = [_BAR, "Triggered Analysis", _BAR, ""]
    flagged = [
        row for row in model.rows if row.analysis is not None and row.analysis.triggered_rules
    ]
    if not flagged:
        lines.append(_colorize("  No active triggers", _COLOR_GRAY, color))
        lines.append("")
        return lines
    for row in flagged:
        lines.append(_receiver_label(row))
        for outcome in row.analysis.triggered_rules:  # type: ignore[union-attr]
            lines.append(f"  • {_rule_label(outcome.name)}")
        lines.append("")
    return lines


def _render_event_log(
    model: DashboardModel, color: bool, max_events: int
) -> list[str]:
    lines = [_BAR, "Event Log", _BAR, ""]
    events = list(model.events)[: max(0, max_events)]
    if not events:
        lines.append(_colorize("  (no events yet)", _COLOR_GRAY, color))
        return lines
    for event in events:
        timestamp = datetime.fromtimestamp(event.wall_time).strftime("%H:%M:%S")
        lines.append(f"  {timestamp}  {event.message}")
    return lines


def _enable_windows_ansi() -> None:
    """Best-effort: let ANSI escapes work on legacy Windows consoles.

    Modern Windows Terminal/PowerShell already do this; older cmd.exe
    windows need ENABLE_VIRTUAL_TERMINAL_PROCESSING turned on explicitly.
    Uses only ctypes (stdlib) - no colorama dependency. Failure here just
    means colors/redraw silently degrade; it must never crash the app.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001 - best-effort only
        pass


class LiveDashboard(ABC):
    """Renders the current health of all receivers, once per tick."""

    def open(self) -> None:
        """Called once before the first update(). Default: no-op."""
        return None

    @abstractmethod
    def update(self, model: DashboardModel) -> None:
        ...

    def close(self) -> None:
        """Called once after the last update(). Default: no-op."""
        return None


class NullLiveDashboard(LiveDashboard):
    """Renders nothing (headless / tests)."""

    def update(self, model: DashboardModel) -> None:
        return None


class TerminalLiveDashboard(LiveDashboard):
    """A fixed, redraws-in-place terminal dashboard (htop/btop-style).

    `clear=True` (default) enables the in-place TUI redraw whenever
    stdout is an actual terminal; pass `clear=False` to always use the
    plain, scrolling, append-only rendering instead (e.g. when capturing
    a session transcript, where cursor-positioning escapes would look
    broken on replay). Non-terminal stdout (systemd/journald, redirected
    to a file, piped) always uses the plain rendering regardless of this
    flag, since there is no screen to redraw in place.
    """

    def __init__(self, clear: bool = True) -> None:
        self._clear = clear
        self._max_lines_drawn = 0

    def _in_place(self) -> bool:
        return self._clear and sys.stdout.isatty()

    def open(self) -> None:
        self._max_lines_drawn = 0
        if self._in_place():
            _enable_windows_ansi()
            sys.stdout.write("\x1b[2J\x1b[H\x1b[?25l")  # clear once, home, hide cursor
            sys.stdout.flush()

    def close(self) -> None:
        if self._in_place():
            sys.stdout.write("\x1b[?25h\n")  # restore the cursor
            sys.stdout.flush()

    def update(self, model: DashboardModel) -> None:
        in_place = self._in_place()
        lines = self._lines(model, color=in_place)
        if in_place:
            self._draw_in_place(lines)
        else:
            print("\n".join(lines), flush=True)

    def render_frame(self, model: DashboardModel) -> str:
        """Plain (uncoloured) frame text - the stable, testable contract."""
        return "\n".join(self._lines(model, color=False))

    # -- internals ------------------------------------------------------

    def _lines(self, model: DashboardModel, color: bool) -> list[str]:
        fixed = [
            *_render_header(model, color),
            *_render_system_status(model, color),
            *_render_receiver_status(model, color),
            *_render_triggered_analysis(model, color),
        ]
        _, term_rows = shutil.get_terminal_size(fallback=(100, 40))
        budget = max(
            _MIN_EVENT_LINES, term_rows - len(fixed) - _EVENT_LOG_CHROME_LINES
        )
        return fixed + _render_event_log(model, color, max_events=budget)

    def _draw_in_place(self, lines: list[str]) -> None:
        _, term_rows = shutil.get_terminal_size(fallback=(100, 40))
        height = min(max(len(lines), self._max_lines_drawn), max(1, term_rows - 1))
        self._max_lines_drawn = height
        padded = lines + [""] * max(0, height - len(lines))
        # Cursor home (never a full clear - that's what causes flicker),
        # then erase-to-end-of-line on every row so a line that got
        # shorter can't leave stale trailing characters behind.
        out = ["\x1b[H"]
        out.extend(f"{line}\x1b[K\n" for line in padded)
        sys.stdout.write("".join(out))
        sys.stdout.flush()
