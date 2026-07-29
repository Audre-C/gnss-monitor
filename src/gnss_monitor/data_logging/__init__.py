"""Optional GNSS data-logging subsystem: raw NMEA archive, parsed CSV, and
periodic per-receiver snapshot CSV.

Completely separate from gnss_monitor.logging_setup (the app.log
diagnostics stream) and gnss_monitor.live.event_log (the on-screen Event
Log): this package archives the measurements themselves, not what the
application decided about them, for offline threshold tuning, replay,
and post-event analysis. Disabled by default; see
gnss_monitor.config.schema.DataLoggingConfig.
"""

from gnss_monitor.data_logging.logger import DataLogger
from gnss_monitor.data_logging.records import (
    ParsedMessageRecord,
    RawSentenceRecord,
    SnapshotRecord,
)

__all__ = [
    "DataLogger",
    "ParsedMessageRecord",
    "RawSentenceRecord",
    "SnapshotRecord",
]
