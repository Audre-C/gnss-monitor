"""Simple Mode controller: the single-threaded orchestration loop.

The controller builds one ReceiverMonitor and one PositionEvaluator per
configured channel, then runs a sequential poll/evaluate/render loop:

    each tick:
        for each receiver: read a batch of lines, update state
        evaluate every receiver against its expected baseline
        render the dashboard
        stop when every source is exhausted

There is no threading and no message bus: for replay files a sequential
loop is simpler, deterministic, and easy to test. The only thing tied to
"file replay" is _build_source(); swapping in a live serial source later
changes that one method and nothing else in this file.
"""

from __future__ import annotations

import time
from typing import Optional

from gnss_monitor.config.schema import ChannelConfig, RootConfig
from gnss_monitor.monitor.dashboard import (
    Dashboard,
    MonitorView,
    TerminalDashboard,
)
from gnss_monitor.monitor.evaluator import EvaluationResult, PositionEvaluator
from gnss_monitor.monitor.receiver_monitor import ReceiverMonitor
from gnss_monitor.sources.base import NMEASource
from gnss_monitor.sources.file_source import FileReplaySource


class SimpleModeController:
    """Runs the Simple Mode health monitor over configured channels."""

    def __init__(
        self,
        config: RootConfig,
        dashboard: Optional[Dashboard] = None,
        tick_interval_s: float = 0.5,
        lines_per_tick: int = 50,
        once: bool = False,
    ) -> None:
        self._config = config
        self._dashboard = dashboard or TerminalDashboard()
        self._tick_interval_s = tick_interval_s
        self._lines_per_tick = lines_per_tick
        self._once = once

        self._monitors: list[ReceiverMonitor] = []
        self._evaluators: dict[str, PositionEvaluator] = {}

        for channel in config.channels:
            source = self._build_source(channel)
            monitor = ReceiverMonitor(
                receiver_id=channel.id,
                display_name=channel.display_name,
                source=source,
            )
            self._monitors.append(monitor)
            self._evaluators[channel.id] = PositionEvaluator(
                config.effective_baseline(channel.id)
            )

    @staticmethod
    def _build_source(channel: ChannelConfig) -> NMEASource:
        """Construct the data source for a channel.

        This is the ONLY place aware of concrete source types. Adding
        live serial input later means adding one branch here; nothing
        else in Simple Mode changes.
        """
        source = channel.source
        if source.type == "file":
            return FileReplaySource(
                source_id=channel.id,
                path=source.path,
                loop=source.loop,
            )
        raise ValueError(
            f"Simple Mode currently supports only file sources; "
            f"channel '{channel.id}' uses source type '{source.type}'"
        )

    def _current_views(self) -> list[MonitorView]:
        views: list[MonitorView] = []
        for monitor in self._monitors:
            result = self._evaluators[monitor.receiver_id].evaluate(
                monitor.state
            )
            views.append(
                MonitorView(name=monitor.display_name, result=result)
            )
        return views

    def _render(self) -> None:
        self._dashboard.update(
            self._config.site.expected, self._current_views()
        )

    def results(self) -> dict[str, EvaluationResult]:
        """Return the latest evaluation for each receiver (by id)."""
        return {
            m.receiver_id: self._evaluators[m.receiver_id].evaluate(
                m.state
            )
            for m in self._monitors
        }

    def run(self) -> dict[str, EvaluationResult]:
        """Run the poll/evaluate/render loop until all sources exhaust.

        Returns the final evaluation results keyed by receiver id.
        """
        for monitor in self._monitors:
            monitor.source.open()
        try:
            while True:
                any_progress = False
                for monitor in self._monitors:
                    if monitor.poll(self._lines_per_tick) > 0:
                        any_progress = True

                if not self._once:
                    self._render()

                if all(m.source.is_exhausted for m in self._monitors):
                    break
                if not any_progress:
                    break
                if self._tick_interval_s > 0:
                    time.sleep(self._tick_interval_s)

            if self._once:
                self._render()
        finally:
            for monitor in self._monitors:
                monitor.source.close()

        return self.results()