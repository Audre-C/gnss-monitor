"""Individual scoring indicators ("rules") for the Version 2 engine.

Each single-receiver rule is a pure function: given the receiver's
current sample (and, for delta-based rules, its previous one) plus the
relevant slice of AnalysisConfig, it returns a RuleOutcome describing
whether it triggered, how many points it contributed, and a
human-readable reason. Pure functions keep every indicator independently
testable and make adding a new one (C/N0, UBX diagnostics, proprietary
Quectel diagnostics - all deliberately not implemented yet) a matter of
writing one more function, never touching the others.

disagreement() is the one indicator that needs every receiver's current
position rather than just one, so it takes a full id -> (lat, lon)
mapping instead of a single sample; AnalysisEvaluator is what assembles
that mapping.

Design decisions worth knowing when reading this file:

* Two-threshold indicators with a single configured weight (speed,
  hdop) score 0 below the lower threshold, the full weight at/above the
  upper one, and ramp up linearly in between - so both configured
  numbers affect the outcome instead of one of them being dead
  configuration. Position is the exception: it has two independently
  configured weights (weight_warning / weight_failure), so it is a
  discrete step at each radius, not a ramp.
* satellites has two independent conditions (an absolute floor and a
  relative drop) sharing one weight: either firing scores the full
  weight once, not additively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gnss_monitor.analysis.state import ReceiverSample, shortest_time_delta_s
from gnss_monitor.config.schema import (
    DisagreementScoringConfig,
    HdopScoringConfig,
    NoFixScoringConfig,
    PositionScoringConfig,
    SatellitesScoringConfig,
    SpeedScoringConfig,
    TimeScoringConfig,
)
from gnss_monitor.geo import haversine_m

_MPS_TO_KMH = 3.6


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """The result of evaluating one scoring indicator."""

    name: str
    triggered: bool
    points: float
    reason: str


def _ramp(value: float, low: float, high: float, weight: float) -> float:
    """Linear ramp: 0 at/below `low`, `weight` at/above `high`."""
    if value <= low:
        return 0.0
    if value >= high:
        return weight
    return weight * (value - low) / (high - low)


def no_fix(sample: ReceiverSample, config: NoFixScoringConfig) -> RuleOutcome:
    """Flat penalty while the receiver has no valid GNSS fix."""
    if not sample.has_fix:
        return RuleOutcome("no_fix", True, config.weight, "no GNSS fix")
    return RuleOutcome("no_fix", False, 0.0, "GNSS fix present")


def position_offset(
    sample: ReceiverSample,
    config: PositionScoringConfig,
    expected_latitude_deg: float,
    expected_longitude_deg: float,
) -> RuleOutcome:
    """Distance from the surveyed expected position, in two tiers.

    Deliberately silent (not triggered) when there is no fix, so this
    never doubles up with no_fix() for the same underlying problem.
    """
    if (
        not sample.has_fix
        or sample.latitude_deg is None
        or sample.longitude_deg is None
    ):
        return RuleOutcome(
            "position_offset", False, 0.0, "no fix; position not evaluated"
        )
    distance_m = haversine_m(
        expected_latitude_deg,
        expected_longitude_deg,
        sample.latitude_deg,
        sample.longitude_deg,
    )
    if distance_m > config.failure_radius_m:
        return RuleOutcome(
            "position_offset",
            True,
            config.weight_failure,
            f"{distance_m:.0f} m from expected exceeds failure radius "
            f"{config.failure_radius_m:.0f} m",
        )
    if distance_m > config.warning_radius_m:
        return RuleOutcome(
            "position_offset",
            True,
            config.weight_warning,
            f"{distance_m:.0f} m from expected exceeds warning radius "
            f"{config.warning_radius_m:.0f} m",
        )
    return RuleOutcome(
        "position_offset",
        False,
        0.0,
        f"{distance_m:.0f} m from expected (within tolerance)",
    )


def sudden_speed(
    sample: ReceiverSample, config: SpeedScoringConfig
) -> RuleOutcome:
    """Reported speed for a receiver whose antenna is fixed in place."""
    if sample.speed_mps is None:
        return RuleOutcome("sudden_speed", False, 0.0, "no speed data available")
    speed_kmh = sample.speed_mps * _MPS_TO_KMH
    points = _ramp(
        speed_kmh,
        config.stationary_speed_limit_kmh,
        config.warning_speed_kmh,
        config.weight,
    )
    if points > 0.0:
        return RuleOutcome(
            "sudden_speed",
            True,
            points,
            f"speed {speed_kmh:.1f} km/h exceeds stationary limit "
            f"{config.stationary_speed_limit_kmh:.1f} km/h (warning at "
            f"{config.warning_speed_kmh:.1f} km/h)",
        )
    return RuleOutcome(
        "sudden_speed",
        False,
        0.0,
        f"speed {speed_kmh:.1f} km/h within stationary limit "
        f"{config.stationary_speed_limit_kmh:.1f} km/h",
    )


def satellite_anomaly(
    sample: ReceiverSample,
    prior: Optional[ReceiverSample],
    config: SatellitesScoringConfig,
) -> RuleOutcome:
    """Too few satellites, or a sudden drop from the previous reading."""
    if sample.num_satellites is None:
        return RuleOutcome(
            "satellite_anomaly", False, 0.0, "no satellite count available"
        )
    reasons: list[str] = []
    if sample.num_satellites < config.minimum:
        reasons.append(
            f"{sample.num_satellites} below minimum {config.minimum}"
        )
    if prior is not None and prior.num_satellites is not None:
        drop = prior.num_satellites - sample.num_satellites
        if drop >= config.sudden_drop:
            reasons.append(
                f"dropped by {drop} (from {prior.num_satellites} to "
                f"{sample.num_satellites})"
            )
    if reasons:
        return RuleOutcome(
            "satellite_anomaly", True, config.weight, "; ".join(reasons)
        )
    return RuleOutcome(
        "satellite_anomaly",
        False,
        0.0,
        f"{sample.num_satellites} satellites (normal)",
    )


def hdop_anomaly(
    sample: ReceiverSample, config: HdopScoringConfig
) -> RuleOutcome:
    """Horizontal dilution of precision, ramping between warning/critical."""
    if sample.hdop is None:
        return RuleOutcome("hdop_anomaly", False, 0.0, "no HDOP data available")
    points = _ramp(sample.hdop, config.warning, config.critical, config.weight)
    if points > 0.0:
        return RuleOutcome(
            "hdop_anomaly",
            True,
            points,
            f"HDOP {sample.hdop:.1f} elevated (warning {config.warning:.1f}, "
            f"critical {config.critical:.1f})",
        )
    return RuleOutcome(
        "hdop_anomaly",
        False,
        0.0,
        f"HDOP {sample.hdop:.1f} below warning threshold {config.warning:.1f}",
    )


def time_discontinuity(
    sample: ReceiverSample,
    prior: Optional[ReceiverSample],
    config: TimeScoringConfig,
) -> RuleOutcome:
    """A jump - forward or backward - in reported UTC time between fixes."""
    if sample.utc_time_s is None or prior is None or prior.utc_time_s is None:
        return RuleOutcome(
            "time_discontinuity",
            False,
            0.0,
            "not enough timestamp history yet",
        )
    jump = abs(shortest_time_delta_s(sample.utc_time_s, prior.utc_time_s))
    if jump > config.max_jump_seconds:
        return RuleOutcome(
            "time_discontinuity",
            True,
            config.weight,
            f"timestamp jumped {jump:.1f}s, exceeds "
            f"{config.max_jump_seconds:.1f}s",
        )
    return RuleOutcome(
        "time_discontinuity",
        False,
        0.0,
        f"timestamp advanced {jump:.1f}s (normal)",
    )


def disagreement(
    receiver_id: str,
    positions: dict[str, tuple[float, float]],
    config: DisagreementScoringConfig,
) -> RuleOutcome:
    """Distance of this receiver's fix from its peers' centroid.

    `positions` should only contain receivers eligible to participate
    (i.e. not reference/multi-constellation receivers - see
    AnalysisEvaluator). Needs at least one other receiver with a fix to
    compare against.
    """
    if receiver_id not in positions:
        return RuleOutcome(
            "disagreement", False, 0.0, "no fix; disagreement not evaluated"
        )
    others = [
        position for rid, position in positions.items() if rid != receiver_id
    ]
    if not others:
        return RuleOutcome(
            "disagreement",
            False,
            0.0,
            "no other receivers with a fix to compare against",
        )
    centroid_lat = sum(p[0] for p in others) / len(others)
    centroid_lon = sum(p[1] for p in others) / len(others)
    lat, lon = positions[receiver_id]
    distance_m = haversine_m(lat, lon, centroid_lat, centroid_lon)
    if distance_m > config.max_distance_between_receivers_m:
        return RuleOutcome(
            "disagreement",
            True,
            config.weight,
            f"{distance_m:.0f} m from the other receivers' centroid "
            f"exceeds {config.max_distance_between_receivers_m:.0f} m",
        )
    return RuleOutcome(
        "disagreement",
        False,
        0.0,
        f"{distance_m:.0f} m from the other receivers' centroid "
        "(within tolerance)",
    )
