"""Terminal dashboard for live multi-receiver monitoring.

Displays one row per receiver with its GNSS constellation, health status,
and current latitude/longitude. The layout is fully data-driven: it renders
whatever list of LiveRow values the controller supplies, so adding or
removing receivers is purely a configuration change and never touches this
module.

Rendering is decoupled from the workers and evaluator; the controller
passes in the expected baseline and the rows.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 70
_OK_MARK = "\u2713"
_FAIL_MARK = "\u2717"
_WAIT_MARK = "\u2026"


@dataclass(frozen=True)
class LiveRow:
    """One receiver's data for one dashboard frame."""

    name: str
    constellation: str
    connection: ConnectionStatus
    result: EvaluationResult
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _coord_cell(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.6f}"


def _status_cell(row: LiveRow) -> str:
    if row.connection is ConnectionStatus.DISCONNECTED:
        return f"{_FAIL_MARK} OFFLINE"
    if row.connection is ConnectionStatus.CONNECTING:
        return f"{_WAIT_MARK} CONN"
    status = row.result.status
    if status is HealthStatus.OK:
        return f"{_OK_MARK} OK"
    if status is HealthStatus.NO_DATA:
        return f"{_WAIT_MARK} WAIT"
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
    """Clears the terminal and prints a fresh live frame each update."""

    def __init__(self, clear: bool = True) -> None:
        self._clear = clear

    def update(
        self,
        baseline: ExpectedBaseline,
        rows: Sequence[LiveRow],
    ) -> None:
        frame = self.render_frame(baseline, rows)
        if self._clear:
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
            f"{'Receiver':<13}{'GNSS':<11}{'Status':<11}"
            f"{'Latitude':<14}{'Longitude':<14}"
        )
        lines = [
            bar,
            "GNSS HEALTH MONITOR (LIVE)",
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
                f"{_coord_cell(row.latitude_deg):<14}"
                f"{_coord_cell(row.longitude_deg):<14}"
            )
        lines.append(rule)
        return "\n".join(lines)