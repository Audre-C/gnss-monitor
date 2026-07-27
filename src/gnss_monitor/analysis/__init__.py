"""Version 2 scoring engine: configurable, multi-indicator health analysis.

Replaces Simple Mode's single "position error > threshold" check with a
weighted-scoring engine that produces one of four health states (OK,
Warning, Potential Spoofing, Spoofing Detected) from several independent
indicators, all configured via the optional `analysis:` YAML section
(see gnss_monitor.config.schema.AnalysisConfig). Wired into live mode via
LiveController + adapters.receiver_sample_from_state(); Simple Mode
(replay) is unaffected and continues to use monitor.evaluator only.
"""

from gnss_monitor.analysis.adapters import receiver_sample_from_state
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
    "receiver_sample_from_state",
    "shortest_time_delta_s",
    "total_score",
]
