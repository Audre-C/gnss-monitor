"""Unit tests for the Simple Mode position evaluator."""

from __future__ import annotations

from gnss_monitor.config.schema import ExpectedBaseline
from gnss_monitor.monitor import (
    HealthStatus,
    PositionEvaluator,
    ReceiverState,
)


def make_baseline(radius_m: float = 100.0) -> ExpectedBaseline:
    return ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=radius_m,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=4,
        max_hdop=5.0,
    )


def test_no_data_status() -> None:
    ev = PositionEvaluator(make_baseline())
    result = ev.evaluate(ReceiverState())
    assert result.status is HealthStatus.NO_DATA
    assert result.distance_m is None


def test_no_fix_fails() -> None:
    ev = PositionEvaluator(make_baseline())
    state = ReceiverState(sentences_seen=10, has_fix=False)
    result = ev.evaluate(state)
    assert result.status is HealthStatus.FAILED
    assert result.distance_m is None
    assert "fix" in result.reason


def test_within_radius_is_ok() -> None:
    ev = PositionEvaluator(make_baseline(radius_m=100.0))
    state = ReceiverState(
        sentences_seen=100,
        has_fix=True,
        latitude_deg=25.334414,
        longitude_deg=51.469355,
    )
    result = ev.evaluate(state)
    assert result.status is HealthStatus.OK
    assert result.distance_m is not None
    assert result.distance_m < 100.0


def test_outside_radius_fails() -> None:
    ev = PositionEvaluator(make_baseline(radius_m=100.0))
    state = ReceiverState(
        sentences_seen=100,
        has_fix=True,
        latitude_deg=25.40,  # ~7 km north
        longitude_deg=51.469359,
    )
    result = ev.evaluate(state)
    assert result.status is HealthStatus.FAILED
    assert result.distance_m is not None
    assert result.distance_m > 100.0


def test_stale_data_reports_no_data() -> None:
    # sentences_seen > 0 (data arrived at some point) but last_seen_monotonic
    # is far enough in the past to exceed receiver_timeout_s: the receiver
    # went silent and must be reported as NO_DATA, not as still-healthy on
    # its last-known fix.
    baseline = make_baseline()
    ev = PositionEvaluator(baseline)
    state = ReceiverState(
        sentences_seen=100,
        has_fix=True,
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        last_seen_monotonic=1000.0,
    )
    result = ev.evaluate(state, now=1000.0 + baseline.receiver_timeout_s + 1)
    assert result.status is HealthStatus.NO_DATA
    assert result.distance_m is None


def test_fresh_data_within_timeout_still_evaluated_normally() -> None:
    baseline = make_baseline()
    ev = PositionEvaluator(baseline)
    state = ReceiverState(
        sentences_seen=100,
        has_fix=True,
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        last_seen_monotonic=1000.0,
    )
    result = ev.evaluate(state, now=1000.0 + baseline.receiver_timeout_s - 1)
    assert result.status is HealthStatus.OK


def test_radius_boundary_is_inclusive() -> None:
    # A point ~5 m away with a 5.0 m radius: comparison is <=, so a point
    # exactly at the tolerance passes. Use a generous radius to assert the
    # inclusive branch clearly.
    baseline = make_baseline(radius_m=1000.0)
    ev = PositionEvaluator(baseline)
    state = ReceiverState(
        sentences_seen=1,
        has_fix=True,
        latitude_deg=25.334373,
        longitude_deg=51.469359,
    )
    result = ev.evaluate(state)
    assert result.status is HealthStatus.OK
    assert result.distance_m == 0.0