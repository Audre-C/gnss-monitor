"""Terminal dashboard for Simple Mode.

Renders a clean, refreshable table of receiver health. Rendering is
decoupled from monitoring and evaluation: the controller passes in the
expected baseline and a list of MonitorView rows, and the dashboard is
responsible only for turning those into text.

Two implementations are provided:
    * TerminalDashboard - clears the screen and prints a frame each tick.
    * NullDashboard     - does nothing (used by tests and headless runs).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 50
_NAME_COL = 16
_OK_MARK = "\u2713"  # check mark
_FAIL_MARK = "\u2717"  # ballot X


def _fit(text: str, width: int) -> str:
    """Truncate text to width (with an ellipsis) so columns stay aligned."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


@dataclass(frozen=True)
class MonitorView:
    """A single row of the dashboard."""

    name: str
    result: EvaluationResult


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
        if self._clear:
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
            return f"{_FAIL_MARK} NO DATA"
        return f"{_FAIL_MARK} FAIL"

    @staticmethod
    def _distance_cell(distance_m: Optional[float]) -> str:
        if distance_m is None:
            return "-"
        return f"{distance_m:.1f} m"

    def render_frame(
        self,
        baseline: ExpectedBaseline,
        views: Sequence[MonitorView],
    ) -> str:
        bar = "=" * _WIDTH
        rule = "-" * _WIDTH
        lines = [
            bar,
            "GNSS HEALTH MONITOR (Simple Mode)",
            bar,
            "Expected Position",
            f"  {baseline.latitude_deg:.6f}",
            f"  {baseline.longitude_deg:.6f}",
            "Allowed Radius",
            f"  {baseline.position_tolerance_m:.0f} m",
            rule,
            f"{'Receiver':<{_NAME_COL}}{'Status':<12}{'Distance':<12}",
            rule,
        ]
        for view in views:
            lines.append(
                f"{_fit(view.name, _NAME_COL - 1):<{_NAME_COL}}"
                f"{self._status_cell(view.result):<12}"
                f"{self._distance_cell(view.result.distance_m):<12}"
            )
        lines.append(rule)
        return "\n".join(lines)