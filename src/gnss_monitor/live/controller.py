"""Live mode controller: concurrent multi-receiver monitoring.

Builds one ReceiverWorker (own thread) and one PositionEvaluator per
configured channel, starts them, and runs a render loop that snapshots
every worker, evaluates it against its expected baseline, and repaints the
dashboard. Workers run independently: a receiver that disconnects keeps
retrying while the others continue, and nothing crashes the application.

Only _default_source_factory knows about concrete source types. Serial vs
file (and Windows COM vs Linux /dev/tty*) are configuration concerns; the
rest of the pipeline is unchanged from replay mode.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from gnss_monitor.config.schema import ChannelConfig, RootConfig
from gnss_monitor.live.dashboard import (
    LiveDashboard,
    LiveRow,
    TerminalLiveDashboard,
)
from gnss_monitor.live.worker import ReceiverSnapshot, ReceiverWorker
from gnss_monitor.monitor.evaluator import EvaluationResult, PositionEvaluator
from gnss_monitor.monitor.receiver_monitor import ReceiverMonitor
from gnss_monitor.sources.base import NMEASource
from gnss_monitor.sources.file_source import FileReplaySource
from gnss_monitor.sources.serial_source import SerialSource

_logger = logging.getLogger("gnss_monitor.live")


class LiveController:
    """Runs concurrent live monitoring over all configured channels."""

    def __init__(
        self,
        config: RootConfig,
        dashboard: Optional[LiveDashboard] = None,
        reconnect_interval_s: float = 3.0,
        read_timeout_s: float = 0.1,
        poll_batch: int = 200,
        refresh_interval_s: float = 0.5,
        source_factory: Optional[
            Callable[[ChannelConfig], NMEASource]
        ] = None,
    ) -> None:
        self._config = config
        self._dashboard = dashboard or TerminalLiveDashboard()
        self._refresh_interval_s = refresh_interval_s
        self._read_timeout_s = read_timeout_s
        self._reconnect_interval_s = reconnect_interval_s
        self._poll_batch = poll_batch
        self._source_factory = source_factory or self._default_source_factory
        self._stop = threading.Event()

        self._workers: list[ReceiverWorker] = []
        self._evaluators: dict[str, PositionEvaluator] = {}

        for channel in config.channels:
            source = self._source_factory(channel)
            monitor = ReceiverMonitor(
                receiver_id=channel.id,
                display_name=channel.display_name,
                source=source,
            )
            worker = ReceiverWorker(
                monitor=monitor,
                port_label=self._port_label(channel),
                reconnect_interval_s=reconnect_interval_s,
                poll_batch=poll_batch,
            )
            self._workers.append(worker)
            self._evaluators[channel.id] = PositionEvaluator(
                config.effective_baseline(channel.id)
            )

    def _default_source_factory(
        self, channel: ChannelConfig
    ) -> NMEASource:
        source = channel.source
        if source.type == "serial":
            return SerialSource(
                source_id=channel.id,
                port=source.port,
                baud=source.baud,
                read_timeout_s=self._read_timeout_s,
            )
        if source.type == "file":
            # Allows replaying a file through the live/threaded path.
            return FileReplaySource(
                source_id=channel.id,
                path=source.path,
                loop=source.loop,
            )
        raise ValueError(
            f"channel '{channel.id}' has unsupported source type "
            f"'{source.type}'"
        )

    @staticmethod
    def _port_label(channel: ChannelConfig) -> str:
        source = channel.source
        if source.type == "serial":
            return source.port
        return "file"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        for worker in self._workers:
            worker.start()

    def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.stop()

    def snapshots(self) -> list[ReceiverSnapshot]:
        return [worker.snapshot() for worker in self._workers]

    def rows(self) -> list[LiveRow]:
        rows: list[LiveRow] = []
        for snap in self.snapshots():
            result = self._evaluators[snap.receiver_id].evaluate(snap.state)
            rows.append(
                LiveRow(
                    name=snap.name,
                    port=snap.port,
                    connection=snap.connection,
                    result=result,
                    fix_quality=snap.state.fix_quality,
                    num_satellites=snap.state.num_satellites,
                    last_nmea_utc=snap.state.last_fix_utc,
                    last_update_wall=snap.last_update_wall,
                )
            )
        return rows

    def results(self) -> dict[str, EvaluationResult]:
        return {
            snap.receiver_id: self._evaluators[
                snap.receiver_id
            ].evaluate(snap.state)
            for snap in self.snapshots()
        }

    def wait_until_sources_exhausted(self, timeout_s: float) -> bool:
        """Block until every source reports exhausted, or timeout.

        Only meaningful for file sources (serial is never exhausted).
        Returns True if all exhausted, False on timeout.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if all(s.source_exhausted for s in self.snapshots()):
                return True
            time.sleep(0.02)
        return False

    def run(self) -> None:
        """Start workers and run the render loop until interrupted."""
        self.start()
        try:
            while not self._stop.is_set():
                self._dashboard.update(
                    self._config.site.expected, self.rows()
                )
                self._stop.wait(self._refresh_interval_s)
        except KeyboardInterrupt:
            _logger.info("Interrupted by user; shutting down.")
        finally:
            self.stop()