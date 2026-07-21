"""End-to-end Simple Mode test: config -> controller -> results.

Runs the full pipeline (file source -> framer -> parser -> evaluator)
exactly as production does, against the real test_data corpus, using a
NullDashboard so no terminal output is produced.
"""

from __future__ import annotations

from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    ExpectedOverride,
    FileSourceConfig,
    RootConfig,
    SiteSection,
)
from gnss_monitor.monitor import (
    HealthStatus,
    NullDashboard,
    SimpleModeController,
)
from tests.fixtures import dataset_path


def build_config(radius_m: float = 100.0) -> RootConfig:
    baseline = ExpectedBaseline(
        latitude_deg=25.334373,
        longitude_deg=51.469359,
        position_tolerance_m=radius_m,
        altitude_m=40.0,
        altitude_tolerance_m=30.0,
        max_speed_mps=1.0,
        receiver_timeout_s=10.0,
        min_satellites=4,
        max_hdop=5.0,
    )
    channels = [
        ChannelConfig(
            id="neo6m_1",
            module="u-blox NEO-6M",
            name="GPS",
            source=FileSourceConfig(
                type="file", path=dataset_path("neo6m", "normal")
            ),
        ),
        ChannelConfig(
            id="lc29h_1",
            module="Quectel LC29HEA",
            name="Multi-GNSS",
            source=FileSourceConfig(
                type="file", path=dataset_path("lc29hea", "normal")
            ),
        ),
        ChannelConfig(
            id="neom10_1",
            module="Quescan NeoM101612F",
            name="M10",
            source=FileSourceConfig(
                type="file", path=dataset_path("neom10", "normal")
            ),
        ),
    ]
    return RootConfig(
        app=AppSection(),
        site=SiteSection(name="Test Site", expected=baseline),
        channels=channels,
    )


def run_once(config: RootConfig):
    controller = SimpleModeController(
        config=config,
        dashboard=NullDashboard(),
        tick_interval_s=0.0,
        lines_per_tick=100_000,
        once=True,
    )
    return controller.run()


def test_all_receivers_healthy_within_radius() -> None:
    results = run_once(build_config(radius_m=100.0))
    assert set(results.keys()) == {"neo6m_1", "lc29h_1", "neom10_1"}
    for rid, result in results.items():
        assert result.status is HealthStatus.OK, (rid, result.reason)
        assert result.distance_m is not None
        assert result.distance_m < 100.0


def test_tight_radius_marks_all_failed() -> None:
    # 1 m radius: real fixes are a few metres out -> all FAIL on position.
    results = run_once(build_config(radius_m=1.0))
    for rid, result in results.items():
        assert result.status is HealthStatus.FAILED, rid
        assert result.distance_m is not None


def test_per_channel_override_applies() -> None:
    config = build_config(radius_m=100.0)
    # Override just one channel to an impossible tolerance.
    config.channels[0].expected = ExpectedOverride(position_tolerance_m=0.5)
    results = run_once(config)
    assert results["neo6m_1"].status is HealthStatus.FAILED
    assert results["lc29h_1"].status is HealthStatus.OK
    assert results["neom10_1"].status is HealthStatus.OK