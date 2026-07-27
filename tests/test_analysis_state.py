"""Unit tests for gnss_monitor.analysis.state."""

from __future__ import annotations

import pytest

from gnss_monitor.analysis.state import (
    ReceiverHistory,
    ReceiverSample,
    parse_nmea_utc_seconds,
    shortest_time_delta_s,
)


class TestParseNmeaUtcSeconds:
    def test_parses_hhmmss(self) -> None:
        assert parse_nmea_utc_seconds("113954.00") == pytest.approx(
            11 * 3600 + 39 * 60 + 54.0
        )

    def test_midnight(self) -> None:
        assert parse_nmea_utc_seconds("000000.00") == pytest.approx(0.0)

    def test_none_input_returns_none(self) -> None:
        assert parse_nmea_utc_seconds(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_nmea_utc_seconds("") is None

    def test_malformed_string_returns_none(self) -> None:
        assert parse_nmea_utc_seconds("not-a-time") is None


class TestShortestTimeDeltaS:
    def test_normal_forward_tick(self) -> None:
        assert shortest_time_delta_s(11.0, 10.0) == pytest.approx(1.0)

    def test_backward_jump(self) -> None:
        assert shortest_time_delta_s(5.0, 10.0) == pytest.approx(-5.0)

    def test_midnight_rollover_is_short_forward_step(self) -> None:
        # 23:59:59 -> 00:00:01 should read as +2s, not -86398s.
        previous = 23 * 3600 + 59 * 60 + 59.0
        current = 1.0
        assert shortest_time_delta_s(current, previous) == pytest.approx(2.0)

    def test_rollover_backward(self) -> None:
        previous = 1.0
        current = 23 * 3600 + 59 * 60 + 59.0
        assert shortest_time_delta_s(current, previous) == pytest.approx(-2.0)


class TestReceiverHistory:
    def test_starts_empty(self) -> None:
        history = ReceiverHistory()
        assert history.current is None
        assert history.previous is None

    def test_first_push_sets_current_only(self) -> None:
        history = ReceiverHistory()
        sample = ReceiverSample(has_fix=True)
        history.push(sample)
        assert history.current is sample
        assert history.previous is None

    def test_second_push_demotes_current_to_previous(self) -> None:
        history = ReceiverHistory()
        first = ReceiverSample(num_satellites=10)
        second = ReceiverSample(num_satellites=4)
        history.push(first)
        history.push(second)
        assert history.current is second
        assert history.previous is first
