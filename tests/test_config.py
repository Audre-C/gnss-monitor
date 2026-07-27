"""Unit tests for configuration loading, validation, and baseline merging."""

from __future__ import annotations

from pathlib import Path

import pytest

from gnss_monitor.config import ConfigError, load_config

VALID_CONFIG = """
app:
  name: gnss-monitor-test
  log_dir: logs
  diagnostics_level: DEBUG

site:
  name: "Test Site"
  expected:
    latitude_deg: 1.352100
    longitude_deg: 103.819800
    position_tolerance_m: 50.0
    altitude_m: 45.0
    altitude_tolerance_m: 30.0
    max_speed_mps: 1.0
    receiver_timeout_s: 10.0
    min_satellites: 4
    max_hdop: 5.0

channels:
  - id: neo6m_1
    module: "u-blox NEO-6M"
    antenna: "BT-104"
    source:
      type: file
      path: data/neo6m.nmea
      rate: realtime

  - id: lc29h_1
    module: "Quectel LC29HEA"
    source:
      type: serial
      port: COM7
      baud: 115200
    expected:
      position_tolerance_m: 20.0
      min_satellites: 8
"""


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestValidConfig:
    def test_loads_successfully(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        assert config.app.name == "gnss-monitor-test"
        assert config.site.name == "Test Site"
        assert len(config.channels) == 2

    def test_source_types_discriminated(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        assert config.channels[0].source.type == "file"
        assert config.channels[1].source.type == "serial"
        assert config.channels[1].source.port == "COM7"
        assert config.channels[1].source.baud == 115200

    def test_baseline_without_override_is_site_baseline(
        self, tmp_path: Path
    ) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        baseline = config.effective_baseline("neo6m_1")
        assert baseline.position_tolerance_m == 50.0
        assert baseline.min_satellites == 4
        assert baseline.latitude_deg == pytest.approx(1.352100)

    def test_baseline_with_override_merges(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        baseline = config.effective_baseline("lc29h_1")
        assert baseline.position_tolerance_m == 20.0
        assert baseline.min_satellites == 8
        assert baseline.latitude_deg == pytest.approx(1.352100)
        assert baseline.max_hdop == 5.0
        assert baseline.receiver_timeout_s == 10.0

    def test_unknown_channel_id_raises(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        with pytest.raises(KeyError):
            config.effective_baseline("does_not_exist")


class TestInvalidConfig:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "missing.yaml")

    def test_empty_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="empty"):
            load_config(write_config(tmp_path, ""))

    def test_invalid_yaml_syntax(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="invalid YAML"):
            load_config(write_config(tmp_path, "channels: [unclosed"))

    def test_missing_site_section(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.replace("site:", "not_site:")
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))

    def test_latitude_out_of_range(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.replace(
            "latitude_deg: 1.352100", "latitude_deg: 99.0"
        )
        with pytest.raises(ConfigError, match="latitude"):
            load_config(write_config(tmp_path, text))

    def test_negative_tolerance_rejected(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.replace(
            "position_tolerance_m: 50.0", "position_tolerance_m: -5.0"
        )
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))

    def test_duplicate_channel_ids_rejected(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.replace("id: lc29h_1", "id: neo6m_1")
        with pytest.raises(ConfigError, match="duplicate"):
            load_config(write_config(tmp_path, text))

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + "\nunexpected_section: true\n"
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))

    def test_no_channels_rejected(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.split("channels:")[0] + "channels: []\n"
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))

ANALYSIS_SECTION = """
analysis:
  position:
    warning_radius_m: 100
    failure_radius_m: 500
    weight_warning: 20
    weight_failure: 40
  no_fix:
    weight: 30
  speed:
    stationary_speed_limit_kmh: 5
    warning_speed_kmh: 10
    weight: 25
  satellites:
    minimum: 5
    sudden_drop: 3
    weight: 15
  hdop:
    warning: 2.5
    critical: 5.0
    weight: 20
  time:
    max_jump_seconds: 3
    weight: 30
  disagreement:
    max_distance_between_receivers_m: 100
    weight: 35
  overall:
    warning: 30
    potential_spoofing: 60
    spoofing: 90
"""


class TestAnalysisConfig:
    def test_absent_by_default(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        assert config.analysis is None

    def test_loads_when_present(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION
        config = load_config(write_config(tmp_path, text))
        assert config.analysis is not None
        assert config.analysis.position.warning_radius_m == 100
        assert config.analysis.position.failure_radius_m == 500
        assert config.analysis.overall.spoofing == 90

    def test_position_radii_must_be_ordered(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "failure_radius_m: 500", "failure_radius_m: 50"
        )
        with pytest.raises(ConfigError, match="failure_radius_m"):
            load_config(write_config(tmp_path, text))

    def test_speed_thresholds_must_be_ordered(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "warning_speed_kmh: 10", "warning_speed_kmh: 2"
        )
        with pytest.raises(ConfigError, match="warning_speed_kmh"):
            load_config(write_config(tmp_path, text))

    def test_hdop_thresholds_must_be_ordered(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "critical: 5.0", "critical: 1.0"
        )
        with pytest.raises(ConfigError, match="critical"):
            load_config(write_config(tmp_path, text))

    def test_overall_thresholds_must_be_ordered(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "potential_spoofing: 60", "potential_spoofing: 10"
        )
        with pytest.raises(ConfigError, match="overall"):
            load_config(write_config(tmp_path, text))

    def test_unknown_analysis_key_rejected(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "weight: 30\n  speed:", "weight: 30\n    bogus: 1\n  speed:"
        )
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))


SIGNAL_SECTION = """  signal:
    min_tracked_satellites: 4
    uniform_std_dbhz: 2.0
    uniform_min_mean_dbhz: 40
    max_mean_cn0_dbhz: 50
    weight: 30
"""


class TestSignalScoringConfig:
    def test_absent_by_default(self, tmp_path: Path) -> None:
        # analysis: without a signal: section must still load - existing
        # configs written before this indicator existed are unaffected.
        text = VALID_CONFIG + ANALYSIS_SECTION
        config = load_config(write_config(tmp_path, text))
        assert config.analysis is not None
        assert config.analysis.signal is None

    def test_loads_when_present(self, tmp_path: Path) -> None:
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "  overall:", SIGNAL_SECTION + "  overall:"
        )
        config = load_config(write_config(tmp_path, text))
        assert config.analysis.signal is not None
        assert config.analysis.signal.min_tracked_satellites == 4
        assert config.analysis.signal.uniform_std_dbhz == 2.0
        assert config.analysis.signal.uniform_min_mean_dbhz == 40
        assert config.analysis.signal.max_mean_cn0_dbhz == 50
        assert config.analysis.signal.weight == 30

    def test_min_tracked_satellites_must_be_at_least_one(
        self, tmp_path: Path
    ) -> None:
        signal_section = SIGNAL_SECTION.replace(
            "min_tracked_satellites: 4", "min_tracked_satellites: 0"
        )
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "  overall:", signal_section + "  overall:"
        )
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))

    def test_uniform_std_dbhz_must_be_positive(self, tmp_path: Path) -> None:
        signal_section = SIGNAL_SECTION.replace(
            "uniform_std_dbhz: 2.0", "uniform_std_dbhz: 0"
        )
        text = VALID_CONFIG + ANALYSIS_SECTION.replace(
            "  overall:", signal_section + "  overall:"
        )
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, text))


class TestConstellation:
    def test_constellation_field_loads(self, tmp_path: Path) -> None:
        text = VALID_CONFIG.replace(
            'module: "u-blox NEO-6M"',
            'module: "u-blox NEO-6M"\n    constellation: "GPS"',
        )
        config = load_config(write_config(tmp_path, text))
        assert config.channels[0].constellation == "GPS"
        assert config.channels[0].display_constellation == "GPS"

    def test_constellation_defaults_to_dash(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, VALID_CONFIG))
        # No constellation set -> display helper yields '-'.
        assert config.channels[0].constellation is None
        assert config.channels[0].display_constellation == "-"