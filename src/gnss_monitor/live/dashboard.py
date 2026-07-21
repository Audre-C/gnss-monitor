"""Terminal dashboard for live multi-receiver monitoring.

Richer than the replay dashboard: adds Port, Fix quality, satellite count,
last NMEA time, and last update time, and shows connection status
(connecting / connected / disconnected) alongside the health verdict.

Rendering is decoupled from the workers and evaluator: the controller
passes in the expected baseline and a list of LiveRow values.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.live.worker import ConnectionStatus
from gnss_monitor.model import FixQuality
from gnss_monitor.monitor.evaluator import EvaluationResult, HealthStatus

_WIDTH = 84
_OK_MARK = "\u2713"
_FAIL_MARK = "\u2717"
_WAIT_MARK = "\u2026"

_FIX_LABELS = {
    FixQuality.INVALID: "none",
    FixQuality.GPS: "GPS",
    FixQuality.DGPS: "DGPS",
    FixQuality.PPS: "PPS",
    FixQuality.RTK_FIXED: "RTK",
    FixQuality.RTK_FLOAT: "RTKf",
    FixQuality.ESTIMATED: "est",
    FixQuality.MANUAL: "man",
    FixQuality.SIMULATION: "sim",
}


@dataclass(frozen=True)
class LiveRow:
    """One receiver's data for one dashboard frame."""

    name: str
    port: str
    connection: ConnectionStatus
    result: EvaluationResult
    fix_quality: Optional[FixQuality]
    num_satellites: Optional[int]
    last_nmea_utc: Optional[str]
    last_update_wall: Optional[float]


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "\u2026"


def _fix_cell(fix: Optional[FixQuality]) -> str:
    if fix is None:
        return "-"
    return _FIX_LABELS.get(fix, "?")


def _sats_cell(n: Optional[int]) -> str:
    return "-" if n is None else str(n)


def _nmea_time_cell(utc: Optional[str]) -> str:
    if not utc or len(utc) < 6:
        return "-"
    return f"{utc[0:2]}:{utc[2:4]}:{utc[4:6]}"


def _updated_cell(wall: Optional[float]) -> str:
    if wall is None:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(wall))


def _distance_cell(distance_m: Optional[float]) -> str:
    return "-" if distance_m is None else f"{distance_m:.1f} m"


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
            f"{'Receiver':<18}{'Port':<8}{'Status':<11}{'Distance':<10}"
            f"{'Fix':<6}{'Sats':<5}{'NMEA':<10}{'Updated':<9}"
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
                f"{_fit(row.name, 17):<18}"
                f"{_fit(row.port, 7):<8}"
                f"{_status_cell(row):<11}"
                f"{_distance_cell(row.result.distance_m):<10}"
                f"{_fix_cell(row.fix_quality):<6}"
                f"{_sats_cell(row.num_satellites):<5}"
                f"{_nmea_time_cell(row.last_nmea_utc):<10}"
                f"{_updated_cell(row.last_update_wall):<9}"
            )
        lines.append(rule)
        return "\n".join(lines)