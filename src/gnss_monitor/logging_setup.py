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


def setup_logging(log_dir: Path, level: str, console: bool = True) -> logging.Logger:
    """Configure and return the application root logger.

    Creates the log directory if necessary. Logs go to a rotating file
    (log_dir/app.log) always, and additionally to the console
    (StreamHandler, which defaults to stderr) when console=True. Safe to
    call once at startup.

    Pass console=False when an interactive TUI is about to take over the
    terminal: over a plain SSH session, stdout and stderr share the same
    physical screen, so any log line written to stderr - a reconnect
    retry warning, a state-change notice - lands on the terminal outside
    of the dashboard's own cursor-controlled redraw, which either forces
    the terminal to scroll or leaves stray text behind. The rotating
    file still gets everything regardless; only the screen echo is
    suppressed. Non-interactive runs (systemd/journald, redirected
    output) keep the console handler, since that's what journalctl -f
    depends on for both the dashboard frames and diagnostics.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gnss_monitor")
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers if called twice (e.g. in tests).
    if logger.handlers:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if console:
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