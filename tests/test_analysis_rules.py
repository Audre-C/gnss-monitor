"""Unit tests for the individual scoring indicators in gnss_monitor.analysis.rules."""

from __future__ import annotations

import pytest

from gnss_monitor.analysis import rules
from gnss_monitor.analysis.state import ReceiverSample
from gnss_monitor.config.schema import (
    DisagreementScoringConfig,
    HdopScoringConfig,
    NoFixScoringConfig,
    PositionScoringConfig,
    SatellitesScoringConfig,
    SpeedScoringConfig,
    TimeScoringConfig,
)

EXPECTED_LAT = 25.334373
EXPECTED_LON = 51.469359


def position_config() -> PositionScoringConfig:
    return PositionScoringConfig(
        warning_radius_m=100.0,
        failure_radius_m=500.0,
        weight_warning=20.0,
        weight_failure=40.0,
    )


def speed_config() -> SpeedScoringConfig:
    return SpeedScoringConfig(
        stationary_speed_limit_kmh=5.0,
        warning_speed_kmh=10.0,
        weight=25.0,
    )


def satellites_config() -> SatellitesScoringConfig:
    return SatellitesScoringConfig(minimum=5, sudden_drop=3, weight=15.0)


def hdop_config() -> HdopScoringConfig:
    return HdopScoringConfig(warning=2.5, critical=5.0, weight=20.0)


def time_config() -> TimeScoringConfig:
    return TimeScoringConfig(max_jump_seconds=3.0, weight=30.0)


def disagreement_config() -> DisagreementScoringConfig:
    return DisagreementScoringConfig(
        max_distance_between_receivers_m=100.0, weight=35.0
    )


class TestNoFix:
    def test_triggers_without_fix(self) -> None:
        outcome = rules.no_fix(
            ReceiverSample(has_fix=False), NoFixScoringConfig(weight=30.0)
        )
        assert outcome.triggered is True
        assert outcome.points == 30.0

    def test_does_not_trigger_with_fix(self) -> None:
        outcome = rules.no_fix(
            ReceiverSample(has_fix=True), NoFixScoringConfig(weight=30.0)
        )
        assert outcome.triggered is False
        assert outcome.points == 0.0


class TestPositionOffset:
    def test_within_tolerance_does_not_trigger(self) -> None:
        outcome = rules.position_offset(
            ReceiverSample(
                has_fix=True, latitude_deg=EXPECTED_LAT, longitude_deg=EXPECTED_LON
            ),
            position_config(),
            EXPECTED_LAT,
            EXPECTED_LON,
        )
        assert outcome.triggered is False

    def test_beyond_warning_radius_scores_warning_weight(self) -> None:
        # ~0.002 deg latitude ~= 222 m: beyond the 100 m warning radius,
        # short of the 500 m failure radius.
        outcome = rules.position_offset(
            ReceiverSample(
                has_fix=True,
                latitude_deg=EXPECTED_LAT + 0.002,
                longitude_deg=EXPECTED_LON,
            ),
            position_config(),
            EXPECTED_LAT,
            EXPECTED_LON,
        )
        assert outcome.triggered is True
        assert outcome.points == 20.0

    def test_beyond_failure_radius_scores_failure_weight(self) -> None:
        outcome = rules.position_offset(
            ReceiverSample(
                has_fix=True,
                latitude_deg=EXPECTED_LAT + 0.02,
                longitude_deg=EXPECTED_LON,
            ),
            position_config(),
            EXPECTED_LAT,
            EXPECTED_LON,
        )
        assert outcome.triggered is True
        assert outcome.points == 40.0

    def test_no_fix_is_not_evaluated(self) -> None:
        outcome = rules.position_offset(
            ReceiverSample(has_fix=False),
            position_config(),
            EXPECTED_LAT,
            EXPECTED_LON,
        )
        assert outcome.triggered is False
        assert outcome.points == 0.0


class TestSuddenSpeed:
    def test_below_stationary_limit_does_not_trigger(self) -> None:
        outcome = rules.sudden_speed(
            ReceiverSample(speed_mps=1.0 / 3.6), speed_config()  # 1 km/h
        )
        assert outcome.triggered is False

    def test_missing_speed_does_not_trigger(self) -> None:
        outcome = rules.sudden_speed(ReceiverSample(speed_mps=None), speed_config())
        assert outcome.triggered is False
        assert outcome.points == 0.0

    def test_at_or_above_warning_speed_scores_full_weight(self) -> None:
        outcome = rules.sudden_speed(
            ReceiverSample(speed_mps=20.0 / 3.6), speed_config()  # 20 km/h
        )
        assert outcome.triggered is True
        assert outcome.points == 25.0

    def test_between_thresholds_scores_partial_weight(self) -> None:
        # Halfway between 5 km/h and 10 km/h -> half of the 25-point weight.
        outcome = rules.sudden_speed(
            ReceiverSample(speed_mps=7.5 / 3.6), speed_config()
        )
        assert outcome.triggered is True
        assert outcome.points == pytest.approx(12.5)


class TestSatelliteAnomaly:
    def test_below_minimum_triggers(self) -> None:
        outcome = rules.satellite_anomaly(
            ReceiverSample(num_satellites=3), None, satellites_config()
        )
        assert outcome.triggered is True
        assert outcome.points == 15.0

    def test_at_or_above_minimum_with_no_prior_does_not_trigger(self) -> None:
        outcome = rules.satellite_anomaly(
            ReceiverSample(num_satellites=8), None, satellites_config()
        )
        assert outcome.triggered is False

    def test_sudden_drop_triggers_even_above_minimum(self) -> None:
        outcome = rules.satellite_anomaly(
            ReceiverSample(num_satellites=6),
            ReceiverSample(num_satellites=10),
            satellites_config(),
        )
        assert outcome.triggered is True
        assert outcome.points == 15.0

    def test_small_drop_does_not_trigger(self) -> None:
        outcome = rules.satellite_anomaly(
            ReceiverSample(num_satellites=9),
            ReceiverSample(num_satellites=10),
            satellites_config(),
        )
        assert outcome.triggered is False

    def test_missing_count_does_not_trigger(self) -> None:
        outcome = rules.satellite_anomaly(
            ReceiverSample(num_satellites=None), None, satellites_config()
        )
        assert outcome.triggered is False


class TestHdopAnomaly:
    def test_below_warning_does_not_trigger(self) -> None:
        outcome = rules.hdop_anomaly(ReceiverSample(hdop=1.0), hdop_config())
        assert outcome.triggered is False

    def test_at_or_above_critical_scores_full_weight(self) -> None:
        outcome = rules.hdop_anomaly(ReceiverSample(hdop=6.0), hdop_config())
        assert outcome.triggered is True
        assert outcome.points == 20.0

    def test_between_thresholds_scores_partial_weight(self) -> None:
        # Halfway between 2.5 and 5.0 -> half of the 20-point weight.
        outcome = rules.hdop_anomaly(ReceiverSample(hdop=3.75), hdop_config())
        assert outcome.triggered is True
        assert outcome.points == pytest.approx(10.0)

    def test_missing_hdop_does_not_trigger(self) -> None:
        outcome = rules.hdop_anomaly(ReceiverSample(hdop=None), hdop_config())
        assert outcome.triggered is False


class TestTimeDiscontinuity:
    def test_no_prior_sample_does_not_trigger(self) -> None:
        outcome = rules.time_discontinuity(
            ReceiverSample(utc_time_s=100.0), None, time_config()
        )
        assert outcome.triggered is False

    def test_normal_tick_does_not_trigger(self) -> None:
        outcome = rules.time_discontinuity(
            ReceiverSample(utc_time_s=101.0),
            ReceiverSample(utc_time_s=100.0),
            time_config(),
        )
        assert outcome.triggered is False

    def test_forward_jump_triggers(self) -> None:
        outcome = rules.time_discontinuity(
            ReceiverSample(utc_time_s=200.0),
            ReceiverSample(utc_time_s=100.0),
            time_config(),
        )
        assert outcome.triggered is True
        assert outcome.points == 30.0

    def test_backward_jump_also_triggers(self) -> None:
        outcome = rules.time_discontinuity(
            ReceiverSample(utc_time_s=50.0),
            ReceiverSample(utc_time_s=100.0),
            time_config(),
        )
        assert outcome.triggered is True
        assert outcome.points == 30.0


class TestDisagreement:
    def test_no_fix_for_this_receiver_is_not_evaluated(self) -> None:
        outcome = rules.disagreement("a", {}, disagreement_config())
        assert outcome.triggered is False

    def test_no_other_receivers_does_not_trigger(self) -> None:
        outcome = rules.disagreement(
            "a", {"a": (25.0, 51.0)}, disagreement_config()
        )
        assert outcome.triggered is False

    def test_close_agreement_does_not_trigger(self) -> None:
        positions = {"a": (25.334373, 51.469359), "b": (25.334380, 51.469365)}
        outcome = rules.disagreement("a", positions, disagreement_config())
        assert outcome.triggered is False

    def test_large_offset_from_peers_triggers(self) -> None:
        positions = {
            "a": (25.334373, 51.469359),
            "b": (25.35, 51.469359),
            "c": (25.35, 51.469359),
        }
        outcome = rules.disagreement("a", positions, disagreement_config())
        assert outcome.triggered is True
        assert outcome.points == 35.0
