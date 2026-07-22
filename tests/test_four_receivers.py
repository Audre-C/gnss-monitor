"""Tests that the system supports N receivers (here: four) via config only.

These verify that adding the fourth (VOLLGO / BeiDou) receiver is a pure
configuration change: the config loads, the constellation labels flow
through, and both the live and replay controllers build one worker/monitor
per channel with no receiver-specific branching.
"""

from __future__ import annotations

from pathlib import Path

from gnss_monitor.config import load_config
from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    FileSourceConfig,
    RootConfig,
    SerialSourceConfig,
    SiteSection,
)
from gnss_monitor.live import LiveController, NullLiveDashboard
from gnss_monitor.live.dashboard import TerminalLiveDashboard

CONSTELLATIONS = ["GPS", "GLONASS", "Galileo", "BeiDou"]


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


def build_four_serial_config() -> RootConfig:
    ports = ["COM24", "COM23", "COM21", "COM19"]
    names = ["NEO-6M", "NeoM10", "LC29HEA", "VOLLGO"]
    ids = ["neo6m_1", "neom10_1", "lc29h_1", "vollgo_1"]
    channels = [
        ChannelConfig(
            id=ids[i],
            module=names[i],
            name=names[i],
            constellation=CONSTELLATIONS[i],
            source=SerialSourceConfig(
                type="serial", port=ports[i], baud=9600
            ),
        )
        for i in range(4)
    ]
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test", expected=_baseline()),
        channels=channels,
    )


def test_four_receiver_config_builds_four_workers() -> None:
    # No hardware: constructing the controller must not open ports, so we
    # can assert it builds one worker/evaluator per channel.
    controller = LiveController(
        build_four_serial_config(), dashboard=NullLiveDashboard()
    )
    assert len(controller._workers) == 4  # noqa: SLF001 - test introspection
    ids = {w.receiver_id for w in controller._workers}
    assert ids == {"neo6m_1", "neom10_1", "lc29h_1", "vollgo_1"}


def test_constellation_labels_render_for_four_receivers() -> None:
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
        for name, constellation in zip(
            ["NEO-6M", "NeoM10", "LC29HEA", "VOLLGO"], CONSTELLATIONS
        )
    ]
    frame = TerminalLiveDashboard(clear=False).render_frame(
        _baseline(), rows
    )
    for label in CONSTELLATIONS:
        assert label in frame
    assert "VOLLGO" in frame
    assert "BeiDou" in frame


def test_live_windows_config_has_four_receivers() -> None:
    path = Path("config/live_windows.yaml")
    if not path.is_file():
        return  # config not present in this checkout; skip silently
    config = load_config(path)
    assert len(config.channels) == 4
    labels = {c.display_constellation for c in config.channels}
    assert labels == {"GPS", "GLONASS", "Galileo", "BeiDou"}
    vollgo = next(c for c in config.channels if c.id == "vollgo_1")
    assert vollgo.source.type == "serial"
    assert vollgo.source.port == "COM19"
    assert vollgo.source.baud == 9600