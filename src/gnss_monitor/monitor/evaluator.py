"""Plausibility evaluation for Simple Mode.

Given a receiver's latest state and the expected baseline for that
receiver, decide whether it is healthy. Simple Mode's single check is
"is the reported position within the allowed radius of the expected
position?", plus the prerequisites (data received, valid fix).

The evaluator is deliberately small and independent so that additional
checks (altitude, speed, satellite count, staleness, and eventually
spoofing metrics) can be added later without touching acquisition,
parsing, or display.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.geo import haversine_m
from gnss_monitor.monitor.receiver_monitor import ReceiverState


class HealthStatus(Enum):
    """Overall plausibility verdict for a receiver."""

    OK = "OK"
    FAILED = "FAILED"
    NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class EvaluationResult:
    """Outcome of evaluating one receiver's state."""

    status: HealthStatus
    distance_m: Optional[float]
    reason: str


class PositionEvaluator:
    """Evaluates a receiver's latest position against a baseline."""

    def __init__(self, baseline: ExpectedBaseline) -> None:
        self._baseline = baseline

    @property
    def baseline(self) -> ExpectedBaseline:
        return self._baseline

    def evaluate(
        self, state: ReceiverState, now: Optional[float] = None
    ) -> EvaluationResult:
        if state.sentences_seen == 0:
            return EvaluationResult(
                status=HealthStatus.NO_DATA,
                distance_m=None,
                reason="no data received",
            )

        if state.last_seen_monotonic is not None:
            if now is None:
                now = time.monotonic()
            age_s = now - state.last_seen_monotonic
            if age_s > self._baseline.receiver_timeout_s:
                return EvaluationResult(
                    status=HealthStatus.NO_DATA,
                    distance_m=None,
                    reason=(
                        f"no data received in {age_s:.1f}s "
                        f"(timeout {self._baseline.receiver_timeout_s:.0f}s)"
                    ),
                )

        if (
            not state.has_fix
            or state.latitude_deg is None
            or state.longitude_deg is None
        ):
            return EvaluationResult(
                status=HealthStatus.FAILED,
                distance_m=None,
                reason="no valid GNSS fix",
            )

        distance = haversine_m(
            self._baseline.latitude_deg,
            self._baseline.longitude_deg,
            state.latitude_deg,
            state.longitude_deg,
        )

        if distance <= self._baseline.position_tolerance_m:
            return EvaluationResult(
                status=HealthStatus.OK,
                distance_m=distance,
                reason="within expected radius",
            )

        return EvaluationResult(
            status=HealthStatus.FAILED,
            distance_m=distance,
            reason=(
                f"position {distance:.1f} m from expected exceeds "
                f"{self._baseline.position_tolerance_m:.0f} m radius"
            ),
        )