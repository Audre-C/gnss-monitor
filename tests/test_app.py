"""Unit tests for Application: signal handling and lifecycle logging.

These test _handle_signal() directly rather than delivering real OS
signals: real signal delivery timing is inherently racy to test and
differs across platforms (Windows cannot deliver a real SIGTERM the way
POSIX can), whereas _handle_signal is exactly what a real signal
delivery calls, so testing it directly is both deterministic and an
accurate stand-in.
"""

from __future__ import annotations

import signal

from gnss_monitor.app import Application


class FakeController:
    """A minimal stand-in satisfying the Controller protocol."""

    def __init__(self, run_side_effect=None) -> None:
        self.run_calls = 0
        self.stop_requested = False
        self._run_side_effect = run_side_effect

    def run(self) -> None:
        self.run_calls += 1
        if self._run_side_effect is not None:
            self._run_side_effect()

    def request_stop(self) -> None:
        self.stop_requested = True


def test_run_returns_true_on_clean_completion() -> None:
    controller = FakeController()
    assert Application(controller).run() is True
    assert controller.run_calls == 1


def test_run_returns_false_and_swallows_exception_on_crash() -> None:
    def boom() -> None:
        raise RuntimeError("receiver pipeline exploded")

    controller = FakeController(run_side_effect=boom)
    # Must not raise: a crash is reported via the return value, so the
    # caller (cli.py) can pick an exit code without a stack trace
    # escaping to the top level.
    assert Application(controller).run() is False


def test_run_returns_true_on_keyboard_interrupt() -> None:
    def interrupt() -> None:
        raise KeyboardInterrupt()

    controller = FakeController(run_side_effect=interrupt)
    assert Application(controller).run() is True


def test_signal_handler_requests_stop_without_blocking() -> None:
    controller = FakeController()
    app = Application(controller)
    app._handle_signal(signal.SIGTERM, None)  # noqa: SLF001
    assert controller.stop_requested is True


def test_run_installs_and_restores_signal_handlers() -> None:
    prior_sigint = signal.getsignal(signal.SIGINT)
    prior_sigterm = signal.getsignal(signal.SIGTERM)

    Application(FakeController()).run()

    assert signal.getsignal(signal.SIGINT) == prior_sigint
    assert signal.getsignal(signal.SIGTERM) == prior_sigterm


def test_signal_handler_installed_during_run() -> None:
    # While run() is executing, our handler must be the active one so a
    # real SIGTERM/SIGINT is actually caught instead of falling through
    # to Python's default behaviour.
    seen = {}

    def capture_handlers() -> None:
        seen["sigint"] = signal.getsignal(signal.SIGINT)
        seen["sigterm"] = signal.getsignal(signal.SIGTERM)

    app = Application(FakeController(run_side_effect=capture_handlers))
    app.run()

    assert seen["sigint"] == app._handle_signal  # noqa: SLF001
    assert seen["sigterm"] == app._handle_signal  # noqa: SLF001
