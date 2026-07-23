"""Process-level application lifecycle.

This is the one place that knows about OS signals, process exit codes, and
"the application" as a whole rather than any single controller. cli.py's
job stops at turning argv into a configured controller (live or replay);
Application is what actually runs it with a managed startup, main loop,
and shutdown.

Signal handling follows the standard advice for CPython: the handler does
the absolute minimum (ask the controller to stop) and returns immediately.
The actual teardown - closing serial ports, joining worker threads - always
happens back on the main thread inside the controller's own run(), never
inside the handler itself. This keeps a second Ctrl+C / SIGTERM responsive
even while the first one is still being processed.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType
from typing import Optional, Protocol

_SIGNAL_NAMES = {
    signal.SIGINT: "SIGINT",
    signal.SIGTERM: "SIGTERM",
}


class Controller(Protocol):
    """The minimal shape Application needs from a mode controller.

    Both LiveController and SimpleModeController satisfy this: run()
    blocks until the controller decides to stop (source exhaustion, an
    error, or request_stop()) and is responsible for its own setup and
    teardown; request_stop() is safe to call from a signal handler.
    """

    def run(self) -> object: ...

    def request_stop(self) -> None: ...


class Application:
    """Runs a controller with a managed startup/shutdown lifecycle."""

    def __init__(
        self,
        controller: Controller,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._controller = controller
        self._logger = logger or logging.getLogger("gnss_monitor.app")
        self._previous_handlers: dict[int, object] = {}

    def run(self) -> bool:
        """Run the controller to completion. Returns True on a clean exit.

        A clean exit covers both "ran until told to stop" (signal, or the
        controller's own natural end) and Ctrl+C. Returns False only if
        the controller raised, so the caller can exit non-zero and let
        systemd's Restart= policy take over.
        """
        self._install_signal_handlers()
        self._logger.info("gnss-monitor starting")
        try:
            self._controller.run()
        except KeyboardInterrupt:
            # Belt and suspenders: covers the case where something (e.g.
            # a test) runs the controller without going through our
            # SIGINT handler, so Ctrl+C still surfaces as an exception.
            self._logger.info("interrupted (KeyboardInterrupt); stopping")
        except Exception:
            self._logger.exception("gnss-monitor crashed")
            return False
        finally:
            self._restore_signal_handlers()
        self._logger.info("gnss-monitor stopped")
        return True

    # -- signal handling ------------------------------------------------

    def _install_signal_handlers(self) -> None:
        for sig, name in _SIGNAL_NAMES.items():
            try:
                self._previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):
                # Not the main thread, or the platform doesn't support
                # this signal. Ctrl+C still works via KeyboardInterrupt.
                self._logger.debug(
                    "could not install a handler for %s", name
                )

    def _restore_signal_handlers(self) -> None:
        for sig, handler in self._previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        self._previous_handlers.clear()

    def _handle_signal(
        self, signum: int, _frame: Optional[FrameType]
    ) -> None:
        name = _SIGNAL_NAMES.get(signum, str(signum))
        self._logger.info(
            "received %s; requesting graceful shutdown", name
        )
        self._controller.request_stop()
