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
from datetime import datetime
from typing import Callable, Optional

from gnss_monitor import __version__
from gnss_monitor.analysis import (
    AnalysisEvaluator,
    AnalysisResult,
    HealthState,
    receiver_sample_from_state,
)
from gnss_monitor.config.schema import ChannelConfig, RootConfig
from gnss_monitor.live.dashboard import (
    DashboardModel,
    LiveDashboard,
    LiveRow,
    TerminalLiveDashboard,
)
from gnss_monitor.live.event_log import EventLog
from gnss_monitor.live.worker import (
    ConnectionStatus,
    ReceiverSnapshot,
    ReceiverWorker,
)
from gnss_monitor.monitor.evaluator import (
    EvaluationResult,
    HealthStatus,
    PositionEvaluator,
)
from gnss_monitor.monitor.receiver_monitor import ReceiverMonitor
from gnss_monitor.sources.base import NMEASource
from gnss_monitor.sources.file_source import FileReplaySource
from gnss_monitor.sources.serial_source import SerialSource

_logger = logging.getLogger("gnss_monitor.live")

_STATUS_EVENT_LABELS = {
    HealthStatus.OK: "OK",
    HealthStatus.FAILED: "Failed",
    HealthStatus.NO_DATA: "No Data",
}


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
        event_log_capacity: int = 12,
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
        self._started_monotonic: Optional[float] = None
        self._event_log = EventLog(capacity=event_log_capacity)

        self._workers: list[ReceiverWorker] = []
        self._evaluators: dict[str, PositionEvaluator] = {}
        self._constellations: dict[str, str] = {}
        self._last_health: dict[str, HealthStatus] = {}
        self._last_analysis_state: dict[str, HealthState] = {}
        self._last_connection: dict[str, ConnectionStatus] = {}
        self._last_has_fix: dict[str, bool] = {}

        self._analysis_evaluator: Optional[AnalysisEvaluator] = None
        if config.analysis is not None:
            self._analysis_evaluator = AnalysisEvaluator(
                config.analysis,
                expected_latitude_deg=config.site.expected.latitude_deg,
                expected_longitude_deg=config.site.expected.longitude_deg,
                reference_receiver_ids=frozenset(
                    c.id for c in config.channels if c.is_reference
                ),
            )

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
            self._constellations[channel.id] = channel.display_constellation

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
        self._started_monotonic = time.monotonic()
        self._event_log.record("Application started")
        for worker in self._workers:
            worker.start()

    def stop(self) -> None:
        self._stop.set()
        for worker in self._workers:
            worker.stop()

    def request_stop(self) -> None:
        """Ask the run loop to exit; safe to call from a signal handler.

        Only sets the stop flag - it does none of stop()'s teardown work
        (thread joins, port closes), so it never blocks the caller. The
        run loop's own finally: self.stop() performs the actual teardown
        exactly once, on the main thread, outside signal-handler context.
        """
        self._stop.set()

    def snapshots(self) -> list[ReceiverSnapshot]:
        return [worker.snapshot() for worker in self._workers]

    def _event_label(self, receiver_id: str, name: str) -> str:
        """Short, demo-friendly identifier for the Event Log/dashboard.

        Prefers the constellation label ("GPS", "Reference") over the
        receiver's model name ("NEO-6M"), matching how the Receiver
        Status section itself is headed.
        """
        constellation = self._constellations.get(receiver_id, "-")
        return name if constellation == "-" else constellation

    def _record_connection_event(self, snap: ReceiverSnapshot) -> None:
        previous = self._last_connection.get(snap.receiver_id)
        current = snap.connection
        if previous is not None and previous is not current:
            label = self._event_label(snap.receiver_id, snap.name)
            if current is ConnectionStatus.CONNECTED:
                verb = (
                    "recovered"
                    if previous is ConnectionStatus.DISCONNECTED
                    else "connected"
                )
                self._event_log.record(f"{label} {verb}")
            elif current is ConnectionStatus.DISCONNECTED:
                self._event_log.record(f"{label} disconnected")
            # A transition into CONNECTING is just an internal retry
            # step (already logged in detail by ReceiverWorker); it
            # would only add noise to the on-screen event feed.
        self._last_connection[snap.receiver_id] = current

    def _record_fix_event(self, snap: ReceiverSnapshot) -> None:
        previous = self._last_has_fix.get(snap.receiver_id)
        current = snap.state.has_fix
        if previous is not None and previous is not current:
            label = self._event_label(snap.receiver_id, snap.name)
            message = "fix acquired" if current else "fix lost"
            _logger.info("receiver '%s' %s", snap.receiver_id, message)
            self._event_log.record(f"{label} {message}")
        self._last_has_fix[snap.receiver_id] = current

    def _evaluate(self, snap: ReceiverSnapshot) -> EvaluationResult:
        result = self._evaluators[snap.receiver_id].evaluate(snap.state)
        previous = self._last_health.get(snap.receiver_id)
        if previous is not None and previous is not result.status:
            _logger.info(
                "receiver '%s' (%s) status changed: %s -> %s (%s)",
                snap.receiver_id,
                snap.name,
                previous.value,
                result.status.value,
                result.reason,
            )
            label = self._event_label(snap.receiver_id, snap.name)
            status_text = _STATUS_EVENT_LABELS.get(
                result.status, result.status.value
            )
            self._event_log.record(f"{label} {status_text}")
        self._last_health[snap.receiver_id] = result.status
        return result

    def _evaluations(
        self,
    ) -> list[tuple[ReceiverSnapshot, EvaluationResult, bool]]:
        """One (snapshot, Simple Mode result, is-fresh) triple per receiver.

        "fresh" is the single source of truth for "may this receiver's
        data be shown/used right now": a disconnected or stale (NO_DATA)
        receiver's position and analysis score are both withheld, since
        neither is known to be current - see the disconnect-handling
        notes on PositionEvaluator.evaluate.
        """
        evaluations = []
        for snap in self.snapshots():
            result = self._evaluate(snap)
            fresh = (
                snap.connection is not ConnectionStatus.DISCONNECTED
                and result.status is not HealthStatus.NO_DATA
            )
            self._record_connection_event(snap)
            self._record_fix_event(snap)
            evaluations.append((snap, result, fresh))
        return evaluations

    def _analysis_results(
        self,
        evaluations: list[tuple[ReceiverSnapshot, EvaluationResult, bool]],
    ) -> dict[str, AnalysisResult]:
        """Version 2 scores for every currently-fresh receiver.

        Empty if no `analysis:` section is configured. Only fresh
        receivers are fed a new sample: feeding a frozen sample from a
        stale/disconnected receiver would let delta-based rules (sudden
        satellite drop, time discontinuity) compare against a
        gap-spanning "previous" reading and misfire once data resumes.
        """
        if self._analysis_evaluator is None:
            return {}

        for snap, _result, fresh in evaluations:
            if fresh:
                self._analysis_evaluator.update(
                    snap.receiver_id, receiver_sample_from_state(snap.state)
                )

        all_results = self._analysis_evaluator.evaluate_all()
        fresh_ids = {snap.receiver_id for snap, _r, fresh in evaluations if fresh}
        results = {
            receiver_id: result
            for receiver_id, result in all_results.items()
            if receiver_id in fresh_ids
        }
        names = {snap.receiver_id: snap.name for snap, _r, _f in evaluations}
        for receiver_id, result in results.items():
            previous = self._last_analysis_state.get(receiver_id)
            if previous is not None and previous is not result.state:
                _logger.info(
                    "receiver '%s' analysis state changed: %s -> %s "
                    "(score %.0f)",
                    receiver_id,
                    previous.value,
                    result.state.value,
                    result.score,
                )
                label = self._event_label(
                    receiver_id, names.get(receiver_id, receiver_id)
                )
                self._event_log.record(f"{label} {result.state.value}")
            self._last_analysis_state[receiver_id] = result.state
        return results

    def rows(self) -> list[LiveRow]:
        evaluations = self._evaluations()
        analysis_results = self._analysis_results(evaluations)
        rows: list[LiveRow] = []
        for snap, result, fresh in evaluations:
            rows.append(
                LiveRow(
                    name=snap.name,
                    constellation=self._constellations.get(
                        snap.receiver_id, "-"
                    ),
                    connection=snap.connection,
                    result=result,
                    latitude_deg=snap.state.latitude_deg if fresh else None,
                    longitude_deg=(
                        snap.state.longitude_deg if fresh else None
                    ),
                    analysis=analysis_results.get(snap.receiver_id),
                    has_fix=snap.state.has_fix if fresh else None,
                    num_satellites=(
                        snap.state.num_satellites if fresh else None
                    ),
                    hdop=snap.state.hdop if fresh else None,
                    distance_m=result.distance_m if fresh else None,
                    # Deliberately NOT gated by `fresh`: "last update was
                    # 45s ago" while the receiver shows Offline is
                    # genuinely useful diagnostic context, unlike a
                    # position/score which would misrepresent stale data
                    # as current if shown the same way.
                    last_update_wall=snap.last_update_wall,
                )
            )
        return rows

    def results(self) -> dict[str, EvaluationResult]:
        return {
            snap.receiver_id: result
            for snap, result, _fresh in self._evaluations()
        }

    def analysis_results(self) -> dict[str, AnalysisResult]:
        """Latest Version 2 scoring result per receiver with fresh data.

        Empty dict if no `analysis:` section is configured. Mirrors
        results() but for the Version 2 engine.
        """
        return self._analysis_results(self._evaluations())

    def dashboard_model(self) -> DashboardModel:
        """Everything the dashboard needs to render one frame, right now."""
        uptime_s = (
            time.monotonic() - self._started_monotonic
            if self._started_monotonic is not None
            else 0.0
        )
        analysis_mode = (
            "Version 2 Scoring Engine"
            if self._analysis_evaluator is not None
            else "Simple Mode (position only)"
        )
        return DashboardModel(
            title="GNSS Monitoring Platform",
            generated_at=datetime.now(),
            uptime_s=uptime_s,
            app_version=__version__,
            analysis_mode=analysis_mode,
            rows=self.rows(),
            events=self._event_log.recent(),
        )

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
        self._dashboard.open()
        try:
            while not self._stop.is_set():
                self._dashboard.update(self.dashboard_model())
                self._stop.wait(self._refresh_interval_s)
        except KeyboardInterrupt:
            _logger.info("Interrupted by user; shutting down.")
        finally:
            # One last frame showing "stopping" before teardown, so the
            # operator watching the TUI sees a deliberate shutdown rather
            # than the screen just freezing on its last state.
            self._event_log.record("Application stopping")
            self._dashboard.update(self.dashboard_model())
            self.stop()
            self._dashboard.close()