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
    * measures the real terminal size every frame (`shutil.
      get_terminal_size`) and builds a frame that fits inside it: width
      never exceeds the terminal's columns (the receiver table drops its
      lowest-priority columns before it would overflow), and height never
      exceeds the terminal's rows (Triggered Analysis and the Event Log
      shrink, and finally disappear, before the receiver table would be
      pushed past the bottom row). A frame taller than the terminal is
      exactly what forces the terminal itself to scroll out from under a
      fixed-cursor redraw - see _draw_in_place;
    * pads every frame up to the tallest frame drawn so far in this run
      (never past the terminal height), so a frame that's briefly shorter
      can't leave stale text behind or cause the terminal to scroll;
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

_RESET = "\x1b[0m"
_COLOR_GREEN = "\x1b[32m"
_COLOR_YELLOW = "\x1b[33m"
_COLOR_ORANGE = "\x1b[1;33m"  # bold yellow: safe "orange" on 8-color terminals
_COLOR_RED = "\x1b[1;31m"
_COLOR_GRAY = "\x1b[90m"

_COL_SEP = "  "
_MAX_RECEIVER_CELL = 24

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


def _truncate(text: str, max_len: int) -> str:
    """Shorten text to fit max_len, marking the cut with an ellipsis.

    Always operates on plain text - callers must truncate before adding
    any ANSI color codes, since counting escape-sequence bytes toward the
    visible width would either cut them off mid-sequence or truncate the
    visible text too early.
    """
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len == 1:
        return text[:1]
    return text[: max_len - 1] + "…"


def _pack_chunks(chunks: list[tuple[str, str]], width: int) -> list[str]:
    """Greedily pack (plain, display) chunks onto lines <= width wide.

    Never splits a chunk across lines (a "Mode: ..." value never gets cut
    off mid-word by an 80-column wrap) - instead it starts a new line.
    Used for the Header/System Status one-liners so they degrade to two
    or three lines on a narrow terminal instead of overflowing the
    terminal's width. plain is used for width math; display may carry
    ANSI color codes invisible to that math.
    """
    lines: list[str] = []
    # plain_line/display_line accumulate the full line text *including*
    # its leading space from the moment a line starts - not appended
    # separately at the end - so every width comparison below is against
    # the line's true final length. Adding that leading space only after
    # deciding a line fits was an off-by-one that let lines run one
    # column past the terminal's edge.
    plain_line = ""
    display_line = ""
    for plain, display in chunks:
        if len(plain) + 1 > width:
            # A single chunk wider than the whole terminal (e.g. a very
            # long analysis mode name on a tiny terminal): truncate it
            # standalone and drop any color rather than risk cutting an
            # escape sequence in half.
            plain = _truncate(plain, max(0, width - 1))
            display = plain
        piece_plain = f" {plain}" if not plain_line else f"   {plain}"
        piece_display = f" {display}" if not display_line else f"   {display}"
        if plain_line and len(plain_line) + len(piece_plain) > width:
            lines.append(display_line)
            plain_line = f" {plain}"
            display_line = f" {display}"
        else:
            plain_line += piece_plain
            display_line += piece_display
    if plain_line:
        lines.append(display_line)
    return lines


def _rule_label(name: str) -> str:
    return _RULE_LABELS.get(name, name.replace("_", " ").title())


def _receiver_label(row: LiveRow) -> str:
    return row.name if row.constellation in (None, "-") else row.constellation


def _receiver_cell_label(row: LiveRow) -> str:
    """"GPS (NEO-6M)" style identifier for the receiver table's own
    column - shows both the constellation and the physical unit, unlike
    _receiver_label's shorter constellation-only form used in the Event
    Log/Triggered Analysis, where the model name would just be noise."""
    if row.constellation in (None, "-") or row.constellation == row.name:
        return row.name
    return f"{row.constellation} ({row.name})"


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


def _age_text(last_update_wall: Optional[float], now: datetime) -> str:
    if last_update_wall is None:
        return "-"
    updated_at = datetime.fromtimestamp(last_update_wall)
    age_s = max(0.0, (now - updated_at).total_seconds())
    return _format_age(age_s)


# ---------------------------------------------------------------------------
# Sections - each takes (model, color, width[, ...]) and returns the lines
# it owns, already fitted to width. Fixed sections (header/status/table)
# always render in full; the lower-priority sections (triggered analysis,
# event log) additionally take a max_lines budget and shrink - down to
# nothing - to fit whatever height is left over.
# ---------------------------------------------------------------------------


def _render_header(
    model: DashboardModel,
    color: bool,
    width: int,
    term_size: Optional[tuple[int, int]] = None,
) -> list[str]:
    bar = "=" * width
    title = _truncate(f" {model.title}", width)
    chunks = [
        (
            f"Time {model.generated_at:%H:%M:%S}",
            f"Time {model.generated_at:%H:%M:%S}",
        ),
        (
            f"Uptime {_format_duration(model.uptime_s)}",
            f"Uptime {_format_duration(model.uptime_s)}",
        ),
        (f"v{model.app_version}", f"v{model.app_version}"),
    ]
    if term_size is not None:
        text = f"Terminal: {term_size[0]}x{term_size[1]}"
        chunks.append((text, text))
    return [bar, title, *_pack_chunks(chunks, width)]


def _render_system_status(model: DashboardModel, color: bool, width: int) -> list[str]:
    rows = model.rows

    def _count(state: HealthState) -> int:
        return sum(
            1 for row in rows if row.analysis is not None and row.analysis.state is state
        )

    online = sum(1 for row in rows if row.connection is ConnectionStatus.CONNECTED)
    overall = _overall_severity(rows)
    overall_label = _SEVERITY_LABELS[overall]
    warnings = _count(HealthState.WARNING)
    potential = _count(HealthState.POTENTIAL_SPOOFING)
    spoofed = _count(HealthState.SPOOFING_DETECTED)

    chunks = [
        (
            f"Health: {overall_label}",
            f"Health: {_colorize(overall_label, _SEVERITY_COLOR[overall], color)}",
        ),
        (f"Mode: {model.analysis_mode}", f"Mode: {model.analysis_mode}"),
        (f"Online: {online}/{len(rows)}", f"Online: {online}/{len(rows)}"),
        (f"Warnings: {warnings}", f"Warnings: {warnings}"),
        (f"Potential Spoofing: {potential}", f"Potential Spoofing: {potential}"),
        (f"Spoofing Detected: {spoofed}", f"Spoofing Detected: {spoofed}"),
    ]
    rule = "-" * width
    return [rule, *_pack_chunks(chunks, width), ""]


@dataclass(frozen=True)
class _Column:
    key: str
    header: str
    align: str = "left"


# Priority order: earliest columns are kept longest as width tightens.
# Receiver/Status/Score/Lat/Lon are the non-negotiable core - a GNSS
# monitor without a position or a score isn't showing anything useful.
_TABLE_COLUMNS = [
    _Column("receiver", "Receiver"),
    _Column("status", "Status"),
    _Column("score", "Score", "right"),
    _Column("lat", "Lat", "right"),
    _Column("lon", "Lon", "right"),
    _Column("fix", "Fix"),
    _Column("sat", "Sat", "right"),
    _Column("hdop", "HDOP", "right"),
    _Column("dist", "Dist", "right"),
    _Column("age", "Age", "right"),
]
_CORE_COLUMN_COUNT = 5


def _row_table_values(
    row: LiveRow, now: datetime, compact_receiver: bool = False
) -> dict[str, str]:
    receiver_label = _receiver_label(row) if compact_receiver else _receiver_cell_label(row)
    return {
        "receiver": _truncate(receiver_label, _MAX_RECEIVER_CELL),
        "status": _row_status_text(row),
        "score": _score_text(row),
        "lat": _coord_text(row.latitude_deg),
        "lon": _coord_text(row.longitude_deg),
        "fix": _fix_text(row),
        "sat": _int_text(row.num_satellites),
        "hdop": _hdop_text(row.hdop),
        "dist": _distance_text(row.distance_m),
        "age": _age_text(row.last_update_wall, now),
    }


def _select_columns(
    values_per_row: list[dict[str, str]], width: int
) -> tuple[list[_Column], dict[str, int]]:
    """Widest column set that fits width, dropping lowest-priority first.

    Stops shrinking at the core 5 columns even if that still doesn't fit
    - on a terminal too narrow even for that, _render_receiver_table's
    line-level _truncate is the last line of defense.
    """
    columns = list(_TABLE_COLUMNS)
    while True:
        widths = {
            col.key: max(
                len(col.header),
                max((len(v[col.key]) for v in values_per_row), default=0),
            )
            for col in columns
        }
        total = sum(widths.values()) + len(_COL_SEP) * max(0, len(columns) - 1)
        if total <= width or len(columns) <= _CORE_COLUMN_COUNT:
            return columns, widths
        columns = columns[:-1]


def _render_receiver_table(
    model: DashboardModel, color: bool, width: int, max_rows: Optional[int] = None
) -> list[str]:
    bar = "=" * width
    lines = [bar, "Receiver Status", bar, ""]
    rows = model.rows
    if not rows:
        lines.append(_colorize("  No receivers configured", _COLOR_GRAY, color))
        lines.append("")
        return lines

    hidden = 0
    shown_rows = rows
    if max_rows is not None and len(rows) > max(1, max_rows):
        budget = max(1, max_rows - 1) if max_rows > 1 else max_rows
        shown_rows = rows[:budget]
        hidden = len(rows) - len(shown_rows)

    now = model.generated_at
    values_per_row = [_row_table_values(row, now) for row in shown_rows]
    severities = [_row_severity(row) for row in shown_rows]
    columns, widths = _select_columns(values_per_row, width)

    def _row_width(cols: list[_Column], w: dict[str, int]) -> int:
        return sum(w.values()) + len(_COL_SEP) * max(0, len(cols) - 1)

    if _row_width(columns, widths) > width and len(columns) <= _CORE_COLUMN_COUNT:
        # Even the core 5 columns don't fit at full receiver-label width
        # (e.g. "Reference (LC29HEA)" on a ~45-column terminal): drop the
        # parenthetical model name and fall back to the shorter
        # constellation-only label before resorting to raw truncation.
        compact_values = [
            _row_table_values(row, now, compact_receiver=True) for row in shown_rows
        ]
        compact_columns, compact_widths = _select_columns(compact_values, width)
        if _row_width(compact_columns, compact_widths) < _row_width(columns, widths):
            values_per_row, columns, widths = compact_values, compact_columns, compact_widths

    header_cells = [
        _colorize(f"{col.header:<{widths[col.key]}}", _COLOR_GRAY, color) for col in columns
    ]
    # A safety net, not the primary mechanism: _select_columns stops
    # shrinking at the core 5 columns even if their *content* (e.g. a
    # long "Reference (LC29HEA)" receiver label) still doesn't fit a
    # narrow terminal - this truncates the header row to match, the same
    # way each data row already does below.
    lines.append(_truncate(_COL_SEP.join(header_cells).rstrip(), width))
    lines.append("-" * width)

    for values, severity in zip(values_per_row, severities):
        cells = []
        for col in columns:
            raw = values[col.key]
            aligned = (
                f"{raw:<{widths[col.key]}}"
                if col.align == "left"
                else f"{raw:>{widths[col.key]}}"
            )
            if col.key == "status":
                aligned = _colorize(aligned, _SEVERITY_COLOR[severity], color)
            cells.append(aligned)
        lines.append(_truncate(_COL_SEP.join(cells).rstrip(), width))
    if hidden:
        lines.append(
            _truncate(f"  … {hidden} more receiver(s) not shown (enlarge terminal)", width)
        )
    lines.append("")
    return lines


def _render_triggered_analysis(
    model: DashboardModel, color: bool, width: int, max_lines: int
) -> list[str]:
    """Rendered only when something is actually flagged (collapses to
    nothing otherwise) and clipped to whatever height remains after the
    header/status/receiver-table sections - it is lower priority than the
    receiver table but higher than the Event Log.
    """
    flagged = [
        row for row in model.rows if row.analysis is not None and row.analysis.triggered_rules
    ]
    if not flagged or max_lines <= 0:
        return []

    bar = "=" * width
    lines = [bar, "Triggered Analysis", bar, ""]
    for row in flagged:
        lines.append(_truncate(_receiver_label(row), width))
        for outcome in row.analysis.triggered_rules:  # type: ignore[union-attr]
            lines.append(_truncate(f"  • {_rule_label(outcome.name)}", width))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()

    if len(lines) <= max_lines:
        return lines
    if max_lines <= 1:
        return lines[:max_lines]
    return lines[: max_lines - 1] + [_truncate("  … additional triggers not shown", width)]


def _render_event_log(
    model: DashboardModel, color: bool, width: int, max_lines: int
) -> list[str]:
    """Lowest priority section: gets whatever height is left, down to
    nothing (the whole section, including its header, disappears when
    there's no room - unlike the receiver table and Triggered Analysis,
    losing the Event Log doesn't hide anything safety-relevant)."""
    bar = "=" * width
    chrome = [bar, "Event Log", bar]
    if max_lines < len(chrome):
        return []

    lines = list(chrome)
    budget = max_lines - len(chrome)
    events = list(model.events)[:budget]
    if not events:
        if budget > 0:
            lines.append(_colorize("  (no events yet)", _COLOR_GRAY, color))
    else:
        for event in events:
            timestamp = datetime.fromtimestamp(event.wall_time).strftime("%H:%M:%S")
            lines.append(_truncate(f"  {timestamp}  {event.message}", width))
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

    `debug=True` appends the detected terminal size ("Terminal: 120x35")
    to the header - useful when diagnosing why a section got dropped or
    columns got trimmed on a particular SSH client/terminal emulator.
    """

    def __init__(self, clear: bool = True, debug: bool = False) -> None:
        self._clear = clear
        self._debug = debug
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
        cols, term_rows = shutil.get_terminal_size(fallback=(100, 40))
        # cols - 1, not cols: writing exactly `cols` visible characters
        # then our own newline leaves some terminals in a "pending wrap"
        # state that behaves as an extra blank row - trivial to avoid by
        # never claiming the very last column.
        width = max(1, cols - 1)
        available = max(1, term_rows - 1)
        term_size = (cols, term_rows) if self._debug else None

        header = _render_header(model, color, width, term_size)
        status = _render_system_status(model, color, width)
        # The receiver table is the one section that must "always remain
        # visible", so it renders in full first. But "never exceed
        # terminal height" is the harder constraint: if header + status +
        # every receiver row still doesn't fit (a terminal too short for
        # the receiver count), shrink the table's own row count - showing
        # as many receivers as fit plus a "N more not shown" notice -
        # rather than let the frame push past the bottom of the screen.
        table = _render_receiver_table(model, color, width)
        fixed = header + status + table
        if len(fixed) > available:
            max_rows = len(model.rows)
            chrome = len(header) + len(status)
            while max_rows > 1:
                max_rows -= 1
                table = _render_receiver_table(model, color, width, max_rows=max_rows)
                if chrome + len(table) <= available:
                    break
            fixed = header + status + table
        remaining = max(0, available - len(fixed))

        triggered = _render_triggered_analysis(model, color, width, remaining)
        remaining = max(0, remaining - len(triggered))

        events = _render_event_log(model, color, width, remaining) if remaining > 0 else []

        result = fixed + triggered + events
        if len(result) > available:
            # Last resort for a terminal too small even for the header,
            # status, and a one-row table combined: clip rather than let
            # anything force the terminal to scroll.
            result = result[:available]
        return result

    def _draw_in_place(self, lines: list[str]) -> None:
        _, term_rows = shutil.get_terminal_size(fallback=(100, 40))
        cap = max(1, term_rows - 1)
        if len(lines) > cap:
            # Belt and suspenders: _lines() already budgets to fit, but a
            # frame taller than the terminal is exactly what forces the
            # terminal to scroll out from under a fixed cursor-home
            # redraw, so this is enforced again right before the write.
            lines = lines[:cap]
        height = min(max(len(lines), self._max_lines_drawn), cap)
        self._max_lines_drawn = height
        padded = lines + [""] * max(0, height - len(lines))
        # Cursor home (never a full clear - that's what causes flicker),
        # then erase-to-end-of-line on every row so a line that got
        # shorter can't leave stale trailing characters behind.
        out = ["\x1b[H"]
        out.extend(f"{line}\x1b[K\n" for line in padded)
        sys.stdout.write("".join(out))
        sys.stdout.flush()
