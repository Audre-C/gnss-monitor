"""Abstract NMEA data source.

This module defines the single seam that separates *where* raw NMEA data
comes from (a replay file today, a live serial port tomorrow) from
everything downstream (framing, parsing, evaluation, display). Swapping a
FileReplaySource for a SerialSource must require no changes anywhere else
in the platform.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class NMEASource(ABC):
    """A source of raw NMEA text lines.

    A source yields raw sentence lines one at a time and knows nothing
    about NMEA structure. Two behaviours callers rely on:

    * read_line() is non-blocking in spirit: it returns the next line if
      one is available, or None if none is available *right now*. For a
      file that means end-of-file; for a live serial port it means no
      complete line has arrived yet. None must be treated as "nothing to
      process this instant", never as a fatal error.
    * is_exhausted is True only when the source can never produce more
      data (a replay file fully consumed). It is always False for a live
      serial port, which may fall silent but is never "finished".
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier for diagnostics (usually the channel id)."""

    @abstractmethod
    def open(self) -> None:
        """Acquire the underlying resource. Idempotent."""

    @abstractmethod
    def read_line(self) -> Optional[str]:
        """Return the next raw line (without terminators), or None."""

    @property
    @abstractmethod
    def is_exhausted(self) -> bool:
        """True when no further data can ever be produced."""

    @abstractmethod
    def close(self) -> None:
        """Release the underlying resource. Idempotent."""

    def __enter__(self) -> "NMEASource":
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False