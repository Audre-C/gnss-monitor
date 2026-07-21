"""Data sources: turn an origin (file now, serial later) into raw lines."""

from gnss_monitor.sources.base import NMEASource
from gnss_monitor.sources.file_source import FileReplaySource

__all__ = ["NMEASource", "FileReplaySource"]
