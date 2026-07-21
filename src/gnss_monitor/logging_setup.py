"""Application diagnostics logging.

This configures the *diagnostics* stream only (startup, configuration,
warnings, errors). It is deliberately separate from the raw NMEA archive
and the parsed data log, which arrive in Phase 5 as bus consumers.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 5


def setup_logging(log_dir: Path, level: str) -> logging.Logger:
    """Configure and return the application root logger.

    Creates the log directory if necessary. Logs go to both the console
    and a rotating file (log_dir/app.log). Safe to call once at startup.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gnss_monitor")
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers if called twice (e.g. in tests).
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger