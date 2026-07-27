"""Unit tests for gnss_monitor.live.event_log.EventLog."""

from __future__ import annotations

import threading

from gnss_monitor.live.event_log import EventLog


def test_empty_log_has_no_events() -> None:
    assert EventLog().recent() == []


def test_recent_is_newest_first() -> None:
    log = EventLog(capacity=10)
    log.record("first")
    log.record("second")
    log.record("third")
    messages = [event.message for event in log.recent()]
    assert messages == ["third", "second", "first"]


def test_capacity_drops_oldest_first() -> None:
    log = EventLog(capacity=3)
    for i in range(5):
        log.record(f"event {i}")
    messages = [event.message for event in log.recent()]
    assert messages == ["event 4", "event 3", "event 2"]


def test_record_accepts_explicit_wall_time() -> None:
    log = EventLog()
    log.record("seeded", wall_time=1_700_000_000.0)
    assert log.recent()[0].wall_time == 1_700_000_000.0


def test_concurrent_record_does_not_lose_or_duplicate_events() -> None:
    log = EventLog(capacity=1000)
    threads = [
        threading.Thread(target=lambda i=i: log.record(f"e{i}"))
        for i in range(200)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(log.recent()) == 200
    assert len({e.message for e in log.recent()}) == 200
