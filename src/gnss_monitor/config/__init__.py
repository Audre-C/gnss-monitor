"""Configuration package: typed schema and YAML loader."""

from gnss_monitor.config.loader import ConfigError, load_config
from gnss_monitor.config.schema import (
    AppSection,
    ChannelConfig,
    ExpectedBaseline,
    ExpectedOverride,
    FileSourceConfig,
    RootConfig,
    SerialSourceConfig,
    SiteSection,
)

__all__ = [
    "AppSection",
    "ChannelConfig",
    "ConfigError",
    "ExpectedBaseline",
    "ExpectedOverride",
    "FileSourceConfig",
    "RootConfig",
    "SerialSourceConfig",
    "SiteSection",
    "load_config",
]