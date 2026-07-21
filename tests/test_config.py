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