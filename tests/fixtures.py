"""Helpers for locating and reading the permanent test_data corpus.

The project keeps a regression corpus of real receiver captures under
test_data/<receiver>/<scenario>.nmea. Tests read from these files rather
than embedding NMEA strings, so the same data grows with the rooftop
installation and every parser change is validated against real hardware
output.
"""

from __future__ import annotations

from pathlib import Path

# tests/ -> project root -> test_data/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_DATA_DIR = PROJECT_ROOT / "test_data"


def dataset_path(receiver: str, scenario: str = "normal") -> Path:
    """Return the path to test_data/<receiver>/<scenario>.nmea."""
    return TEST_DATA_DIR / receiver / f"{scenario}.nmea"


def read_lines(receiver: str, scenario: str = "normal") -> list[str]:
    """Read a fixture file as a list of raw lines (terminators stripped).

    Blank lines are removed. The file is read with errors='replace' so a
    truncated or partly corrupted final line never breaks loading.
    """
    path = dataset_path(receiver, scenario)
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line for line in text.splitlines() if line.strip() != ""]