"""Unit tests for setup_logging's console on/off toggle.

An interactive live TUI needs stdout (and, since they share a terminal
over plain SSH, stderr) entirely to itself - see TerminalLiveDashboard's
in-place redraw and cli.py's interactive_tui check - so console=False
must mean no StreamHandler at all, only the rotating file handler still
capturing everything.
"""

from __future__ import annotations

import logging
import logging.handlers

from gnss_monitor.logging_setup import setup_logging


def _reset_logger() -> None:
    logger = logging.getLogger("gnss_monitor")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _bare_stream_handlers(logger: logging.Logger) -> list[logging.Handler]:
    # RotatingFileHandler is itself a StreamHandler subclass, so it must
    # be excluded to isolate the console-only handler.
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]


def test_console_true_attaches_a_console_stream_handler(tmp_path) -> None:
    _reset_logger()
    try:
        logger = setup_logging(tmp_path, "INFO", console=True)
        assert len(_bare_stream_handlers(logger)) == 1
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
    finally:
        _reset_logger()


def test_console_false_omits_the_console_handler_but_keeps_the_file(tmp_path) -> None:
    _reset_logger()
    try:
        logger = setup_logging(tmp_path, "INFO", console=False)
        assert _bare_stream_handlers(logger) == []
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
    finally:
        _reset_logger()


def test_console_defaults_to_true_for_non_interactive_callers(tmp_path) -> None:
    _reset_logger()
    try:
        logger = setup_logging(tmp_path, "INFO")
        assert len(_bare_stream_handlers(logger)) == 1
    finally:
        _reset_logger()
