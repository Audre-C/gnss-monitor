"""Version 2 scoring engine: configurable, multi-indicator health analysis.

Replaces Simple Mode's single "position error > threshold" check with a
weighted-scoring engine that produces one of four health states (OK,
Warning, Potential Spoofing, Spoofing Detected) from several independent
indicators, all configured via the optional `analysis:` YAML section
(see gnss_monitor.config.schema.AnalysisConfig). Nothing in this package
is wired into the live/replay controllers yet - see evaluator.py's
module docstring for how a later phase does that without refactoring
this one.
"""

from gnss_monitor.analysis.evaluator import AnalysisEvaluator, AnalysisResult
from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState, classify, total_score
from gnss_monitor.analysis.state import (
    ReceiverHistory,
    ReceiverSample,
    parse_nmea_utc_seconds,
    shortest_time_delta_s,
)

__all__ = [
    "AnalysisEvaluator",
    "AnalysisResult",
    "HealthState",
    "ReceiverHistory",
    "ReceiverSample",
    "RuleOutcome",
    "classify",
    "parse_nmea_utc_seconds",
    "shortest_time_delta_s",
    "total_score",
]
