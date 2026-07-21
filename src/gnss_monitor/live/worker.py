"""Per-receiver worker thread for live monitoring.

Each receiver runs in its own thread so that one receiver going silent,
unplugged, or misbehaving never blocks the others. The worker owns a
ReceiverMonitor (which reuses the existing framer/parser/state logic),
drives it in a loop, and handles the connection lifecycle:

    * open the source; on failure, retry every reconnect_interval_s
    * poll for data; update state under a lock
    * on I/O error (unplugged), close, mark DISCONNECTED, and retry
    * exhausted file sources simply idle (used for replay-through-live)

The worker never raises out of its loop and never terminates the process.
State is published via a thread-safe snapshot() the render thread reads.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gnss_monitor.monitor.receiver_monitor import ReceiverMonitor, ReceiverState

_logger = logging.getLogger("gnss_monitor.live")


class ConnectionStatus(Enum):
    """Connection lifecycle state of a receiver's source."""

    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


@dataclass(frozen=True)
class ReceiverSnapshot:
    """Thread-safe, immutable view of a receiver at one instant."""

    receiver_id: str
    name: str
    port: str
    connection: ConnectionStatus
    state: ReceiverState
    last_update_wall: Optional[float]
    source_exhausted: bool


class ReceiverWorker:
    """Runs one receiver's read loop in a background thread."""

    def __init__(
        self,
        monitor: ReceiverMonitor,
        port_label: str,
        reconnect_interval_s: float = 3.0,
        poll_batch: int = 200,
        idle_sleep_s: float = 0.02,
    ) -> None:
        self._monitor = monitor
        self._port_label = port_label
        self._reconnect_interval_s = reconnect_interval_s
        self._poll_batch = poll_batch
        self._idle_sleep_s = idle_sleep_s

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status = ConnectionStatus.CONNECTING
        self._last_update_wall: Optional[float] = None

    @property
    def receiver_id(self) -> str:
        return self._monitor.receiver_id

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"receiver-{self._monitor.receiver_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_s)
            self._thread = None
        try:
            self._monitor.source.close()
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> ReceiverSnapshot:
        with self._lock:
            return ReceiverSnapshot(
                receiver_id=self._monitor.receiver_id,
                name=self._monitor.display_name,
                port=self._port_label,
                connection=self._status,
                state=dataclasses.replace(self._monitor.state),
                last_update_wall=self._last_update_wall,
                source_exhausted=self._monitor.source.is_exhausted,
            )

    # -- internals ----------------------------------------------------------

    def _set_status(self, status: ConnectionStatus) -> None:
        with self._lock:
            self._status = status

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._ensure_open()
                self._pump_once()
            except OSError as exc:
                # Serial disconnect / I/O error: drop, wait, retry.
                _logger.warning(
                    "receiver '%s' I/O error: %s; reconnecting in %.1fs",
                    self._monitor.receiver_id,
                    exc,
                    self._reconnect_interval_s,
                )
                self._set_status(ConnectionStatus.DISCONNECTED)
                self._safe_close()
                self._stop.wait(self._reconnect_interval_s)
            except Exception as exc:  # noqa: BLE001 - never kill the loop
                _logger.error(
                    "receiver '%s' unexpected error: %r",
                    self._monitor.receiver_id,
                    exc,
                )
                self._set_status(ConnectionStatus.DISCONNECTED)
                self._safe_close()
                self._stop.wait(self._reconnect_interval_s)

    def _ensure_open(self) -> None:
        if self._status is ConnectionStatus.CONNECTED:
            return
        self._set_status(ConnectionStatus.CONNECTING)
        self._monitor.source.open()  # may raise OSError
        self._set_status(ConnectionStatus.CONNECTED)
        _logger.info("receiver '%s' connected", self._monitor.receiver_id)

    def _pump_once(self) -> None:
        with self._lock:
            processed = self._monitor.poll(self._poll_batch)
            if processed > 0:
                self._last_update_wall = time.time()
        if processed == 0:
            # No data available right now (idle serial, or exhausted file).
            self._stop.wait(self._idle_sleep_s)

    def _safe_close(self) -> None:
        try:
            self._monitor.source.close()
        except Exception:  # noqa: BLE001
            pass