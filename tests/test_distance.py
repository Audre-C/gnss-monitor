"""Unit tests for FileReplaySource, driven by the real test_data corpus."""

from __future__ import annotations

import pytest

from gnss_monitor.sources import FileReplaySource
from tests.fixtures import dataset_path


def test_reads_all_lines_then_exhausts() -> None:
    path = dataset_path("neom10", "normal")
    expected = [
        ln.rstrip("\r\n")
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
    ]

    src = FileReplaySource("neom10", path)
    src.open()
    read = []
    while True:
        line = src.read_line()
        if line is None:
            break
        read.append(line)
    assert src.is_exhausted is True
    assert read == expected
    src.close()


def test_read_before_open_raises() -> None:
    src = FileReplaySource("x", dataset_path("neo6m"))
    with pytest.raises(RuntimeError):
        src.read_line()


def test_missing_file_raises_on_open() -> None:
    src = FileReplaySource("x", "test_data/does_not_exist.nmea")
    with pytest.raises(FileNotFoundError):
        src.open()


def test_context_manager_opens_and_closes() -> None:
    with FileReplaySource("neo6m", dataset_path("neo6m")) as src:
        assert src.read_line() is not None


def test_loop_does_not_exhaust() -> None:
    src = FileReplaySource("neo6m", dataset_path("neo6m"), loop=True)
    src.open()
    # Read well past the file length; it should keep returning lines.
    seen = 0
    for _ in range(500):
        if src.read_line() is not None:
            seen += 1
    assert seen == 500
    assert src.is_exhausted is False
    src.close()