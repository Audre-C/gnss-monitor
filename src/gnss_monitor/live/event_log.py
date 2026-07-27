"""Bounded event feed for the live TUI dashboard's "Event Log" section.

The dashboard is a fixed screen that redraws in place (see dashboard.py);
it cannot simply print every notable thing that happens the way the
diagnostics logger does; that's exactly the indefinite-scrolling problem
this phase replaces. EventLog is the in-memory, fixed-capacity record of
the most recent human-readable events (connect/disconnect, fix acquired/
lost, health/analysis state changes) that LiveController appends to and
the dashboard renders each frame - independent of, and in addition to,
the existing rotating file log, which keeps the full, unbounded history.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

_DEFAULT_CAPACITY = 12


@dataclass(frozen=True, slots=True)
class LogEvent:
    """One Event Log line."""

    wall_time: float
    message: str


class EventLog:
    """Thread-safe ring buffer of the most recent events.

    Multiple threads may call record() concurrently (today, only
    LiveController's render loop does, but a future per-receiver
    integration might not); recent() is what the render loop calls once
    per frame, and returns newest-first so the dashboard can print it
    directly without re-sorting.
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._lock = threading.Lock()
        self._events: deque[LogEvent] = deque(maxlen=capacity)

    def record(self, message: str, wall_time: Optional[float] = None) -> None:
        event = LogEvent(wall_time if wall_time is not None else time.time(), message)
        with self._lock:
            self._events.append(event)

    def recent(self) -> list[LogEvent]:
        """Newest-first snapshot of the buffer, oldest entries already dropped."""
        with self._lock:
            return list(reversed(self._events))
