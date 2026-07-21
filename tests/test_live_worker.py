"""Tests for ReceiverWorker: threaded reading, snapshots, reconnection."""

from __future__ import annotations

import threading
import time

from gnss_monitor.live.worker import ConnectionStatus, ReceiverWorker
from gnss_monitor.monitor.receiver_monitor import ReceiverMonitor
from gnss_monitor.sources.base import NMEASource


class ScriptedSource(NMEASource):
    """A source that emits queued lines, can raise, and can recover.

    Behaviour is driven by an event list. Each read_line() consumes the
    next queued line; the special sentinel _RAISE triggers an OSError once
    (simulating a disconnect), after which open() must be called again.
    """

    _RAISE = object()

    def __init__(self, script):
        self._script = list(script)
        self._open = False
        self.open_count = 0
        self._lock = threading.Lock()

    @property
    def source_id(self) -> str:
        return "scripted"

    def open(self) -> None:
        with self._lock:
            self._open = True
            self.open_count += 1

    def read_line(self):
        with self._lock:
            if not self._open:
                raise RuntimeError("read before open")
            if not self._script:
                return None
            item = self._script[0]
            if item is self._RAISE:
                self._script.pop(0)
                self._open = False
                raise OSError("simulated disconnect")
            return self._script.pop(0)

    @property
    def is_exhausted(self) -> bool:
        return False

    def close(self) -> None:
        with self._lock:
            self._open = False

    def enqueue(self, line: str) -> None:
        with self._lock:
            self._script.append(line)


VALID_GGA = (
    "$GNGGA,113954.00,2520.06189,N,05128.16076,E,1,08,1.41,"
    "39.1,M,-24.7,M,,*59"
)


def wait_for(predicate, timeout=3.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_worker_reads_and_snapshots_position() -> None:
    src = ScriptedSource([VALID_GGA])
    monitor = ReceiverMonitor("r1", "R1", src)
    worker = ReceiverWorker(monitor, "COM_T", idle_sleep_s=0.005)
    worker.start()
    try:
        assert wait_for(lambda: worker.snapshot().state.has_fix)
        snap = worker.snapshot()
        assert snap.connection is ConnectionStatus.CONNECTED
        assert snap.state.latitude_deg is not None
        assert 25.0 < snap.state.latitude_deg < 26.0
        assert snap.last_update_wall is not None
    finally:
        worker.stop()


def test_worker_reconnects_after_disconnect() -> None:
    # Emit a line, disconnect, then (after reconnection) emit another.
    src = ScriptedSource([VALID_GGA, ScriptedSource._RAISE])
    monitor = ReceiverMonitor("r1", "R1", src)
    worker = ReceiverWorker(
        monitor, "COM_T", reconnect_interval_s=0.05, idle_sleep_s=0.005
    )
    worker.start()
    try:
        # First it connects and reads.
        assert wait_for(lambda: worker.snapshot().state.has_fix)
        # Then the disconnect is observed at least once.
        assert wait_for(
            lambda: src.open_count >= 2, timeout=3.0
        ), "worker did not attempt reconnection"
        # Feed more data post-reconnect; sentence count should grow.
        before = worker.snapshot().state.sentences_seen
        src.enqueue(VALID_GGA)
        assert wait_for(
            lambda: worker.snapshot().state.sentences_seen > before
        )
    finally:
        worker.stop()


def test_worker_stop_is_clean() -> None:
    src = ScriptedSource([])
    monitor = ReceiverMonitor("r1", "R1", src)
    worker = ReceiverWorker(monitor, "COM_T", idle_sleep_s=0.005)
    worker.start()
    time.sleep(0.05)
    worker.stop()  # must return promptly without error
    # A second stop is harmless.
    worker.stop()