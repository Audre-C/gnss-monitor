"""Replay a previously recorded NMEA log file as an NMEASource."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, TextIO, Union

from gnss_monitor.sources.base import NMEASource


class FileReplaySource(NMEASource):
    """Yields lines from an NMEA log file, one per read_line() call.

    When the file is fully consumed, read_line() returns None and
    is_exhausted becomes True. If loop=True, playback restarts from the
    beginning instead of exhausting (useful for long-running demos).
    """

    def __init__(
        self,
        source_id: str,
        path: Union[str, Path],
        loop: bool = False,
    ) -> None:
        self._source_id = source_id
        self._path = Path(path)
        self._loop = loop
        self._handle: Optional[TextIO] = None
        self._exhausted = False

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        if self._handle is not None:
            return
        if not self._path.is_file():
            raise FileNotFoundError(
                f"replay file not found: {self._path}"
            )
        self._handle = self._path.open(
            "r", encoding="utf-8", errors="replace"
        )
        self._exhausted = False

    def read_line(self) -> Optional[str]:
        if self._handle is None:
            raise RuntimeError(
                f"source '{self._source_id}' read before open()"
            )

        line = self._handle.readline()
        if line == "":  # EOF (readline never returns "" for a blank line)
            if self._loop:
                self._handle.seek(0)
                line = self._handle.readline()
                if line == "":
                    self._exhausted = True
                    return None
            else:
                self._exhausted = True
                return None

        return line.rstrip("\r\n")

    @property
    def is_exhausted(self) -> bool:
        return self._exhausted

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None