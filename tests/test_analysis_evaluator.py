"""Unit tests for AnalysisEvaluator: the scoring engine's orchestration layer."""

from __future__ import annotations

from gnss_monitor.analysis.evaluator import AnalysisEvaluator
from gnss_monitor.analysis.score import HealthState
from gnss_monitor.analysis.state import ReceiverSample
from gnss_monitor.config.schema import (
    AnalysisConfig,
    DisagreementScoringConfig,
    HdopScoringConfig,
    NoFixScoringConfig,
    OverallThresholdsConfig,
    PositionScoringConfig,
    SatellitesScoringConfig,
    SignalScoringConfig,
    SpeedScoringConfig,
    TimeScoringConfig,
)

EXPECTED_LAT = 25.334373
EXPECTED_LON = 51.469359


def build_config() -> AnalysisConfig:
    return AnalysisConfig(
        position=PositionScoringConfig(
            warning_radius_m=100.0,
            failure_radius_m=500.0,
            weight_warning=20.0,
            weight_failure=40.0,
        ),
        no_fix=NoFixScoringConfig(weight=30.0),
        speed=SpeedScoringConfig(
            stationary_speed_limit_kmh=5.0, warning_speed_kmh=10.0, weight=25.0
        ),
        satellites=SatellitesScoringConfig(minimum=5, sudden_drop=3, weight=15.0),
        hdop=HdopScoringConfig(warning=2.5, critical=5.0, weight=20.0),
        time=TimeScoringConfig(max_jump_seconds=3.0, weight=30.0),
        disagreement=DisagreementScoringConfig(
            max_distance_between_receivers_m=100.0, weight=35.0
        ),
        overall=OverallThresholdsConfig(
            warning=30.0, potential_spoofing=60.0, spoofing=90.0
        ),
    )


def build_config_with_signal() -> AnalysisConfig:
    """Same as build_config(), plus a signal: section - used only by the
    tests that need signal_anomaly wired in; every other test relies on
    build_config()'s signal=None to prove backward compatibility."""
    return AnalysisConfig(
        position=PositionScoringConfig(
            warning_radius_m=100.0,
            failure_radius_m=500.0,
            weight_warning=20.0,
            weight_failure=40.0,
        ),
        no_fix=NoFixScoringConfig(weight=30.0),
        speed=SpeedScoringConfig(
            stationary_speed_limit_kmh=5.0, warning_speed_kmh=10.0, weight=25.0
        ),
        satellites=SatellitesScoringConfig(minimum=5, sudden_drop=3, weight=15.0),
        hdop=HdopScoringConfig(warning=2.5, critical=5.0, weight=20.0),
        time=TimeScoringConfig(max_jump_seconds=3.0, weight=30.0),
        disagreement=DisagreementScoringConfig(
            max_distance_between_receivers_m=100.0, weight=35.0
        ),
        signal=SignalScoringConfig(
            min_tracked_satellites=4,
            uniform_std_dbhz=2.0,
            uniform_min_mean_dbhz=40.0,
            max_mean_cn0_dbhz=50.0,
            weight=30.0,
        ),
        overall=OverallThresholdsConfig(
            warning=30.0, potential_spoofing=60.0, spoofing=90.0
        ),
    )


def healthy_sample(**overrides) -> ReceiverSample:
    defaults = dict(
        has_fix=True,
        latitude_deg=EXPECTED_LAT,
        longitude_deg=EXPECTED_LON,
        num_satellites=10,
        hdop=1.0,
        speed_mps=0.0,
        utc_time_s=100.0,
    )
    defaults.update(overrides)
    return ReceiverSample(**defaults)


def make_evaluator(reference_ids: frozenset[str] = frozenset()) -> AnalysisEvaluator:
    return AnalysisEvaluator(
        build_config(),
        expected_latitude_deg=EXPECTED_LAT,
        expected_longitude_deg=EXPECTED_LON,
        reference_receiver_ids=reference_ids,
    )


def make_evaluator_with_signal() -> AnalysisEvaluator:
    return AnalysisEvaluator(
        build_config_with_signal(),
        expected_latitude_deg=EXPECTED_LAT,
        expected_longitude_deg=EXPECTED_LON,
    )


class TestHealthyReceiver:
    def test_healthy_receiver_scores_zero_and_is_ok(self) -> None:
        evaluator = make_evaluator()
        evaluator.update("gps", healthy_sample())
        results = evaluator.evaluate_all()
        result = results["gps"]
        assert result.score == 0.0
        assert result.state is HealthState.OK
        assert result.triggered_rules == ()

    def test_only_updated_receivers_appear(self) -> None:
        evaluator = make_evaluator()
        evaluator.update("gps", healthy_sample())
        results = evaluator.evaluate_all()
        assert set(results.keys()) == {"gps"}


class TestNoFixThroughEvaluator:
    def test_no_fix_triggers_and_reaches_warning(self) -> None:
        evaluator = make_evaluator()
        evaluator.update("gps", healthy_sample(has_fix=False, latitude_deg=None, longitude_deg=None))
        result = evaluator.evaluate_all()["gps"]
        assert result.score == 30.0
        assert result.state is HealthState.WARNING
        names = {o.name for o in result.triggered_rules}
        assert names == {"no_fix"}


class TestSatelliteDropAcrossUpdates:
    def test_sudden_drop_only_visible_after_second_update(self) -> None:
        evaluator = make_evaluator()
        evaluator.update("gps", healthy_sample(num_satellites=10))
        first = evaluator.evaluate_all()["gps"]
        assert first.state is HealthState.OK

        evaluator.update("gps", healthy_sample(num_satellites=5))
        second = evaluator.evaluate_all()["gps"]
        assert second.score == 15.0
        assert any(o.name == "satellite_anomaly" for o in second.triggered_rules)


class TestMultipleTriggeredRulesEscalateState:
    def test_no_fix_plus_hdop_reaches_potential_spoofing(self) -> None:
        # no_fix (30) would only be Warning alone; combined with a critical
        # HDOP reading (20) the total (50) is still below 60, so add speed
        # too (25) to cross into Potential Spoofing at 75.
        evaluator = make_evaluator()
        evaluator.update(
            "gps",
            healthy_sample(
                has_fix=False,
                latitude_deg=None,
                longitude_deg=None,
                hdop=6.0,
                speed_mps=20.0 / 3.6,
            ),
        )
        result = evaluator.evaluate_all()["gps"]
        assert result.score == 30.0 + 20.0 + 25.0
        assert result.state is HealthState.POTENTIAL_SPOOFING


_METERS_PER_DEGREE_LAT = 111_195.0  # matches gnss_monitor.geo's mean Earth radius


def _lat_offset_deg(meters: float) -> float:
    """A latitude-only offset of `meters`, for building test fixtures."""
    return meters / _METERS_PER_DEGREE_LAT


class TestDisagreement:
    def test_outlier_receiver_triggers_disagreement(self) -> None:
        # Four receivers agree; a fifth sits 200 m away. Each agreeing
        # receiver's "others" includes the outlier, so it sees ~50 m of
        # pull (200 m / 4 others) - comfortably under the 100 m
        # threshold. The outlier sees the full 200 m from the other
        # four's centroid, which is over threshold.
        evaluator = make_evaluator()
        for receiver_id in ("a", "b", "d", "e"):
            evaluator.update(receiver_id, healthy_sample())
        evaluator.update(
            "c", healthy_sample(latitude_deg=EXPECTED_LAT + _lat_offset_deg(200.0))
        )

        results = evaluator.evaluate_all()
        for receiver_id in ("a", "b", "d", "e"):
            assert not any(
                o.name == "disagreement" for o in results[receiver_id].triggered_rules
            ), receiver_id
        assert any(o.name == "disagreement" for o in results["c"].triggered_rules)

    def test_reference_receiver_excluded_from_disagreement(self) -> None:
        # The reference receiver sits far from the others but must not be
        # scored for disagreement, nor pull the others' centroid toward it.
        evaluator = make_evaluator(reference_ids=frozenset({"ref"}))
        evaluator.update("a", healthy_sample())
        evaluator.update("b", healthy_sample())
        evaluator.update("ref", healthy_sample(latitude_deg=EXPECTED_LAT + 0.05))

        results = evaluator.evaluate_all()
        assert not any(o.name == "disagreement" for o in results["ref"].triggered_rules)
        assert not any(o.name == "disagreement" for o in results["a"].triggered_rules)
        assert not any(o.name == "disagreement" for o in results["b"].triggered_rules)


class TestSignalAnomalyThroughEvaluator:
    def test_signal_not_configured_adds_no_points(self) -> None:
        # Backward compatibility: an analysis: block without a signal:
        # section must score exactly as it did before this indicator
        # existed, even for a sample whose C/N0 would otherwise trigger.
        evaluator = make_evaluator()
        evaluator.update("gps", healthy_sample(cn0_dbhz=(48, 49, 48, 50, 49)))
        result = evaluator.evaluate_all()["gps"]
        assert result.score == 0.0
        assert result.state is HealthState.OK
        assert not any(o.name == "signal_anomaly" for o in result.triggered_rules)

    def test_uniform_high_cn0_raises_score_when_configured(self) -> None:
        evaluator = make_evaluator_with_signal()
        evaluator.update("gps", healthy_sample(cn0_dbhz=(48, 49, 48, 50, 49)))
        result = evaluator.evaluate_all()["gps"]
        assert result.score == 30.0
        assert result.state is HealthState.WARNING
        assert any(o.name == "signal_anomaly" for o in result.triggered_rules)

    def test_reference_receiver_is_still_scored_for_signal(self) -> None:
        # Unlike disagreement, signal_anomaly is a per-receiver check with
        # nothing to compare against another receiver for, so it applies
        # to the reference/multi-constellation receiver too.
        evaluator = AnalysisEvaluator(
            build_config_with_signal(),
            expected_latitude_deg=EXPECTED_LAT,
            expected_longitude_deg=EXPECTED_LON,
            reference_receiver_ids=frozenset({"ref"}),
        )
        evaluator.update("ref", healthy_sample(cn0_dbhz=(48, 49, 48, 50, 49)))
        result = evaluator.evaluate_all()["ref"]
        assert any(o.name == "signal_anomaly" for o in result.triggered_rules)
