"""Terminal dashboard for live multi-receiver monitoring.

Displays one row per receiver with its GNSS constellation, health status,
and current latitude/longitude. The layout is fully data-driven: it renders
whatever list of LiveRow values the controller supplies, so adding or
removing receivers is purely a configuration change and never touches this
module.

Rendering is decoupled from the workers and evaluator; the controller
passes in the expected baseline and the rows.

When a receiver's `analysis` field is populated (Version 2 scoring engine
active and the receiver's data is fresh - see LiveController), the Status
column shows the four-state analysis result (OK / Warning / Potential
Spoofing / Spoofing Detected) instead of the Simple Mode OK/FAIL/NO FIX,
a Score column is added, and a "Triggered Rules" section lists why each
non-OK receiver scored the way it did. Receivers without a populated
`analysis` (no analysis config, or stale/disconnected data) render exactly
as before - this is purely additive, existing Simple-Mode-only deployments
are unaffected.
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from gnss_monitor.analysis.evaluator import AnalysisResult
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 78
_OK_MARK = "\u2713"
_FAIL_MARK = "\u2717"
_WAIT_MARK = "\u2026"
_WARN_MARK = "!"


@dataclass(frozen=True)
class LiveRow:
    """One receiver's data for one dashboard frame."""

    name: str
    constellation: str
    connection: ConnectionStatus
    result: EvaluationResult
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    analysis: Optional[AnalysisResult] = None


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _coord_cell(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.6f}"


def _score_cell(row: LiveRow) -> str:
    return "-" if row.analysis is None else f"{row.analysis.score:.0f}"


def _analysis_status_cell(state: HealthState) -> str:
    if state is HealthState.OK:
        return f"{_OK_MARK} OK"
    if state is HealthState.WARNING:
        return f"{_WARN_MARK} WARN"
    if state is HealthState.POTENTIAL_SPOOFING:
        return f"{_FAIL_MARK} POT-SPF"
    return f"{_FAIL_MARK} SPOOFED"


def _status_cell(row: LiveRow) -> str:
    if row.connection is ConnectionStatus.DISCONNECTED:
        return f"{_FAIL_MARK} OFFLINE"
    if row.connection is ConnectionStatus.CONNECTING:
        return f"{_WAIT_MARK} CONN"
    if row.result.status is HealthStatus.NO_DATA:
        # Staleness gates everything, including the analysis engine: a
        # receiver that has gone quiet must never show a lingering score.
        return f"{_WAIT_MARK} WAIT"
    if row.analysis is not None:
        return _analysis_status_cell(row.analysis.state)
    status = row.result.status
    if status is HealthStatus.OK:
        return f"{_OK_MARK} OK"
    if row.result.distance_m is None:
        return f"{_FAIL_MARK} NO FIX"
    return f"{_FAIL_MARK} FAIL"


class LiveDashboard(ABC):
    @abstractmethod
    def update(
        self,
        baseline: ExpectedBaseline,
        rows: Sequence[LiveRow],
    ) -> None:
        ...


class NullLiveDashboard(LiveDashboard):
    """Renders nothing (headless / tests)."""

    def update(
        self,
        baseline: ExpectedBaseline,
        rows: Sequence[LiveRow],
    ) -> None:
        return None


class TerminalLiveDashboard(LiveDashboard):
    """Clears the terminal and prints a fresh live frame each update.

    Screen-clearing only happens when stdout is an actual terminal. Under
    systemd, stdout is a pipe into journald, not a tty: clearing there
    would just dump raw escape codes into `journalctl`, so it is skipped
    automatically and frames print as a readable scrolling log instead -
    this is what lets `journalctl -u gnss-monitor -f` show current
    receiver health at a glance over SSH.
    """

    def __init__(self, clear: bool = True) -> None:
        self._clear = clear

    def update(
        self,
        baseline: ExpectedBaseline,
        rows: Sequence[LiveRow],
    ) -> None:
        frame = self.render_frame(baseline, rows)
        if self._clear and sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")
        print(frame, flush=True)

    def render_frame(
        self,
        baseline: ExpectedBaseline,
        rows: Sequence[LiveRow],
    ) -> str:
        bar = "=" * _WIDTH
        rule = "-" * _WIDTH
        header = (
            f"{'Receiver':<13}{'GNSS':<11}{'Status':<11}{'Score':<8}"
            f"{'Latitude':<14}{'Longitude':<14}"
        )
        lines = [
            bar,
            "GNSS HEALTH MONITOR (LIVE)",
            f"Updated: {datetime.now():%Y-%m-%d %H:%M:%S}",
            bar,
            "Expected Position",
            f"  Latitude : {baseline.latitude_deg:.6f}",
            f"  Longitude: {baseline.longitude_deg:.6f}",
            f"  Radius   : {baseline.position_tolerance_m:.0f} m",
            rule,
            header,
            rule,
        ]
        for row in rows:
            lines.append(
                f"{_fit(row.name, 12):<13}"
                f"{_fit(row.constellation, 10):<11}"
                f"{_status_cell(row):<11}"
                f"{_score_cell(row):<8}"
                f"{_coord_cell(row.latitude_deg):<14}"
                f"{_coord_cell(row.longitude_deg):<14}"
            )
        lines.append(rule)
        lines.extend(self._triggered_rules_section(rule, rows))
        return "\n".join(lines)

    @staticmethod
    def _triggered_rules_section(
        rule: str, rows: Sequence[LiveRow]
    ) -> list[str]:
        """Build the Triggered Rules detail block for non-OK analysis rows.

        Omitted entirely when no row has a populated `analysis` with at
        least one triggered rule - e.g. analysis isn't configured, or
        every receiver is currently OK.
        """
        flagged = [
            row
            for row in rows
            if row.analysis is not None and row.analysis.triggered_rules
        ]
        if not flagged:
            return []
        lines = ["Triggered Rules", rule]
        for row in flagged:
            assert row.analysis is not None  # narrows type for mypy/readers
            lines.append(
                f"{row.name} - Score: {row.analysis.score:.0f} - "
                f"State: {row.analysis.state.value}"
            )
            for outcome in row.analysis.triggered_rules:
                lines.append(f"  {_OK_MARK} {outcome.reason}")
        lines.append(rule)
        return lines