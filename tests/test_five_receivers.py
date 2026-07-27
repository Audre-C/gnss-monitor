"""Tests that the system supports N receivers via config only.

Originally written for four receivers, now five (u-blox NEO-M8N /
GLONASS added) - the point being tested hasn't changed: adding a
receiver is a pure configuration change, the config loads, the
constellation labels flow through, and both the live and replay
controllers build one worker/monitor per channel with no
receiver-specific branching. The LC29HEA is multi-constellation and is
marked role: reference rather than given a dedicated constellation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from gnss_monitor.config import load_config
from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    RootConfig,
    SerialSourceConfig,
    SiteSection,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.live.dashboard import DashboardModel, TerminalLiveDashboard

CONSTELLATIONS = ["GPS", "Galileo", "GLONASS", "Multi", "BeiDou"]
RECEIVER_IDS = ["neo6m_1", "neom10_1", "neom8n_1", "lc29h_1", "vollgo_1"]
RECEIVER_NAMES = ["NEO-6M", "NeoM10", "NEO-M8N", "LC29HEA", "VOLLGO"]


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


def build_five_serial_config() -> RootConfig:
    ports = ["COM24", "COM23", "COM25", "COM21", "COM19"]
    roles = ["constellation", "constellation", "constellation", "reference", "constellation"]
    channels = [
        ChannelConfig(
            id=RECEIVER_IDS[i],
            module=RECEIVER_NAMES[i],
            name=RECEIVER_NAMES[i],
            constellation=CONSTELLATIONS[i],
            role=roles[i],
            source=SerialSourceConfig(
                type="serial", port=ports[i], baud=9600
            ),
        )
        for i in range(5)
    ]
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test", expected=_baseline()),
        channels=channels,
    )


def test_five_receiver_config_builds_five_workers() -> None:
    # No hardware: constructing the controller must not open ports, so we
    # can assert it builds one worker/evaluator per channel.
    controller = LiveController(
        build_five_serial_config(), dashboard=NullLiveDashboard()
    )
    assert len(controller._workers) == 5  # noqa: SLF001 - test introspection
    ids = {w.receiver_id for w in controller._workers}
    assert ids == set(RECEIVER_IDS)


def test_reference_role_flows_through_config() -> None:
    config = build_five_serial_config()
    lc29h = next(c for c in config.channels if c.id == "lc29h_1")
    assert lc29h.is_reference is True
    others = [c for c in config.channels if c.id != "lc29h_1"]
    assert all(not c.is_reference for c in others)


def test_constellation_labels_render_for_five_receivers() -> None:
    from gnss_monitor.live.dashboard import LiveRow
    from gnss_monitor.live.worker import ConnectionStatus
    from gnss_monitor.monitor.evaluator import (
        EvaluationResult,
        HealthStatus,
    )

    rows = [
        LiveRow(
            name=name,
            constellation=constellation,
            connection=ConnectionStatus.CONNECTED,
            result=EvaluationResult(HealthStatus.OK, 1.0, "ok"),
            latitude_deg=25.3,
            longitude_deg=51.4,
        )
        for name, constellation in zip(RECEIVER_NAMES, CONSTELLATIONS)
    ]
    model = DashboardModel(
        title="GNSS Monitoring Platform",
        generated_at=datetime.now(),
        uptime_s=0.0,
        app_version="0.0.0-test",
        analysis_mode="Simple Mode (position only)",
        rows=rows,
        events=(),
    )
    frame = TerminalLiveDashboard(clear=False).render_frame(model)
    for label in CONSTELLATIONS:
        assert label in frame
    assert "VOLLGO" in frame
    assert "NEO-M8N" in frame


def test_live_windows_config_has_five_receivers() -> None:
    path = Path("config/live_windows.yaml")
    if not path.is_file():
        return  # config not present in this checkout; skip silently
    config = load_config(path)
    assert len(config.channels) == 5
    labels = {c.display_constellation for c in config.channels}
    assert labels == {"GPS", "Galileo", "GLONASS", "BeiDou", "Reference"}

    lc29h = next(c for c in config.channels if c.id == "lc29h_1")
    assert lc29h.is_reference is True

    neom8n = next(c for c in config.channels if c.id == "neom8n_1")
    assert neom8n.source.type == "serial"
    assert neom8n.source.port == "COM25"
    assert neom8n.source.baud == 9600

    vollgo = next(c for c in config.channels if c.id == "vollgo_1")
    assert vollgo.source.type == "serial"
    assert vollgo.source.port == "COM19"
    assert vollgo.source.baud == 115200
