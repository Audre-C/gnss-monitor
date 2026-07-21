"""Data sources: turn an origin (file now, serial now too) into raw lines."""

from gnss_monitor.sources.base import NMEASource
from gnss_monitor.sources.file_source import FileReplaySource
from gnss_monitor.sources.serial_source import SerialSource

__all__ = ["NMEASource", "FileReplaySource", "SerialSource"]