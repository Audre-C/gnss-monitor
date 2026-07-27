"""Unit tests for gnss_monitor.analysis.score."""

from __future__ import annotations

from gnss_monitor.analysis.rules import RuleOutcome
from gnss_monitor.analysis.score import HealthState, classify, total_score
from gnss_monitor.config.schema import OverallThresholdsConfig


def thresholds() -> OverallThresholdsConfig:
    return OverallThresholdsConfig(
        warning=30.0, potential_spoofing=60.0, spoofing=90.0
    )


class TestTotalScore:
    def test_empty_list_is_zero(self) -> None:
        assert total_score([]) == 0.0

    def test_only_triggered_outcomes_count(self) -> None:
        outcomes = [
            RuleOutcome("a", True, 10.0, "x"),
            RuleOutcome("b", False, 999.0, "not triggered, must not count"),
            RuleOutcome("c", True, 5.0, "y"),
        ]
        assert total_score(outcomes) == 15.0


class TestClassify:
    def test_zero_is_ok(self) -> None:
        assert classify(0.0, thresholds()) is HealthState.OK

    def test_just_below_warning_is_ok(self) -> None:
        assert classify(29.9, thresholds()) is HealthState.OK

    def test_at_warning_boundary_is_warning(self) -> None:
        assert classify(30.0, thresholds()) is HealthState.WARNING

    def test_just_below_potential_spoofing_is_warning(self) -> None:
        assert classify(59.9, thresholds()) is HealthState.WARNING

    def test_at_potential_spoofing_boundary(self) -> None:
        assert classify(60.0, thresholds()) is HealthState.POTENTIAL_SPOOFING

    def test_just_below_spoofing_is_potential_spoofing(self) -> None:
        assert classify(89.9, thresholds()) is HealthState.POTENTIAL_SPOOFING

    def test_at_spoofing_boundary(self) -> None:
        assert classify(90.0, thresholds()) is HealthState.SPOOFING_DETECTED

    def test_well_above_spoofing(self) -> None:
        assert classify(500.0, thresholds()) is HealthState.SPOOFING_DETECTED
