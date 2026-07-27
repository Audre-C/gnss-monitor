"""Combine triggered rule outcomes into a total score and a HealthState.

Kept deliberately tiny and separate from evaluator.py: "how do triggered
points become a score, and a score become a state" is a pure function of
numbers and thresholds, with no knowledge of receivers, history, or
config sections beyond OverallThresholdsConfig. That separation is what
makes it trivial to unit test the classification boundaries in isolation
from everything else in the engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.config.schema import OverallThresholdsConfig


class HealthState(Enum):
    """The four Version 2 health states, in ascending severity."""

    OK = "OK"
    WARNING = "Warning"
    POTENTIAL_SPOOFING = "Potential Spoofing"
    SPOOFING_DETECTED = "Spoofing Detected"


def total_score(outcomes: Iterable[RuleOutcome]) -> float:
    """Sum the points of every triggered outcome (untriggered ones are 0)."""
    return sum(outcome.points for outcome in outcomes if outcome.triggered)


def classify(
    score: float, thresholds: OverallThresholdsConfig
) -> HealthState:
    """Map a total score to a HealthState using the configured cut-offs."""
    if score >= thresholds.spoofing:
        return HealthState.SPOOFING_DETECTED
    if score >= thresholds.potential_spoofing:
        return HealthState.POTENTIAL_SPOOFING
    if score >= thresholds.warning:
        return HealthState.WARNING
    return HealthState.OK
