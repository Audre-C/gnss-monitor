"""Terminal dashboard for Simple Mode (replay).

Renders a clean, refreshable table of receiver health with the same
column set as the live dashboard (Receiver, GNSS, Status, Latitude,
Longitude), so both modes look consistent. Rendering is decoupled from
monitoring and evaluation: the controller passes in the expected baseline
and a list of MonitorView rows.

Two implementations are provided:
    * TerminalDashboard - clears the screen and prints a frame each tick.
    * NullDashboard     - does nothing (used by tests and headless runs).
"""

from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 70
_OK_MARK = "\u2713"  # check mark
_FAIL_MARK = "\u2717"  # ballot X
_WAIT_MARK = "\u2026"


def _fit(text: str, width: int) -> str:
    """Truncate text to width (with an ellipsis) so columns stay aligned."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _coord_cell(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.6f}"


@dataclass(frozen=True)
class MonitorView:
    """A single row of the dashboard.

    constellation and latitude/longitude carry defaults so older callers
    constructing MonitorView(name, result) keep working.
    """

    name: str
    result: EvaluationResult
    constellation: str = "-"
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None


class Dashboard(ABC):
    """Renders the current health of all receivers."""

    @abstractmethod
    def update(
        self,
        baseline: ExpectedBaseline,
        views: Sequence[MonitorView],
    ) -> None:
        ...


class NullDashboard(Dashboard):
    """A dashboard that renders nothing (headless / tests)."""

    def update(
        self,
        baseline: ExpectedBaseline,
        views: Sequence[MonitorView],
    ) -> None:
        return None


class TerminalDashboard(Dashboard):
    """Clears the terminal and prints a fresh frame on every update."""

    def __init__(self, clear: bool = True) -> None:
        self._clear = clear

    def update(
        self,
        baseline: ExpectedBaseline,
        views: Sequence[MonitorView],
    ) -> None:
        frame = self.render_frame(baseline, views)
        if self._clear and sys.stdout.isatty():
            self._clear_screen()
        print(frame, flush=True)

    @staticmethod
    def _clear_screen() -> None:
        os.system("cls" if os.name == "nt" else "clear")

    @staticmethod
    def _status_cell(result: EvaluationResult) -> str:
        if result.status is HealthStatus.OK:
            return f"{_OK_MARK} OK"
        if result.status is HealthStatus.NO_DATA:
            return f"{_WAIT_MARK} WAIT"
        if result.distance_m is None:
            return f"{_FAIL_MARK} NO FIX"
        return f"{_FAIL_MARK} FAIL"

    def render_frame(
        self,
        baseline: ExpectedBaseline,
        views: Sequence[MonitorView],
    ) -> str:
        bar = "=" * _WIDTH
        rule = "-" * _WIDTH
        header = (
            f"{'Receiver':<13}{'GNSS':<11}{'Status':<11}"
            f"{'Latitude':<14}{'Longitude':<14}"
        )
        lines = [
            bar,
            "GNSS HEALTH MONITOR (Simple Mode)",
            bar,
            "Expected Position",
            f"  Latitude : {baseline.latitude_deg:.6f}",
            f"  Longitude: {baseline.longitude_deg:.6f}",
            f"  Radius   : {baseline.position_tolerance_m:.0f} m",
            rule,
            header,
            rule,
        ]
        for view in views:
            lines.append(
                f"{_fit(view.name, 12):<13}"
                f"{_fit(view.constellation, 10):<11}"
                f"{self._status_cell(view.result):<11}"
                f"{_coord_cell(view.latitude_deg):<14}"
                f"{_coord_cell(view.longitude_deg):<14}"
            )
        lines.append(rule)
        return "\n".join(lines)