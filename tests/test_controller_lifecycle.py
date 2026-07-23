"""request_stop() tests: both controllers must be interruptible.

This is what backs the "clean termination via Ctrl+C / SIGINT / SIGTERM /
systemctl stop" requirement - Application's signal handler calls
request_stop(), and these tests prove that actually unblocks run().
"""

from __future__ import annotations

import threading
import time

from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    FileSourceConfig,
    RootConfig,
    SiteSection,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.monitor import NullDashboard, SimpleModeController
from tests.fixtures import dataset_path


def _baseline() -> ExpectedBaseline:
    return ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=100.0,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=4,
        max_hdop=5.0,
    )


def _looping_replay_config() -> RootConfig:
    # loop=True so the source never exhausts on its own: run() can only
    # end via request_stop(), isolating exactly what we're testing.
    channel = ChannelConfig(
        id="neo6m_1",
        module="u-blox NEO-6M",
        name="NEO-6M",
        source=FileSourceConfig(
            type="file", path=dataset_path("neo6m", "normal"), loop=True
        ),
    )
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test Site", expected=_baseline()),
        channels=[channel],
    )


def test_simple_mode_controller_request_stop_interrupts_run() -> None:
    controller = SimpleModeController(
        config=_looping_replay_config(),
        dashboard=NullDashboard(),
        tick_interval_s=0.05,
        lines_per_tick=20,
        once=False,
    )
    threading.Timer(0.15, controller.request_stop).start()

    started = time.monotonic()
    controller.run()
    elapsed = time.monotonic() - started

    # Would otherwise loop forever (loop=True); a low ceiling proves
    # request_stop actually broke the loop rather than it exhausting.
    assert elapsed < 3.0


def test_live_controller_request_stop_interrupts_run() -> None:
    controller = LiveController(
        _looping_replay_config(),
        dashboard=NullLiveDashboard(),
        refresh_interval_s=0.05,
    )
    threading.Timer(0.15, controller.request_stop).start()

    started = time.monotonic()
    try:
        controller.run()
    finally:
        controller.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 3.0


def test_live_controller_request_stop_does_not_block() -> None:
    # request_stop() must be safe/fast to call from a signal handler: it
    # should never join worker threads or close ports itself.
    controller = LiveController(
        _looping_replay_config(), dashboard=NullLiveDashboard()
    )
    controller.start()
    try:
        started = time.monotonic()
        controller.request_stop()
        elapsed = time.monotonic() - started
        assert elapsed < 0.1
    finally:
        controller.stop()
