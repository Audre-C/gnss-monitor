"""Per-receiver monitoring: source -> framer -> parser -> latest state.

A ReceiverMonitor owns one NMEASource and reuses the existing Framer and
NmeaParser. It maintains the receiver's latest observed state (most
recent valid position and fix quality). It performs no evaluation and no
display; those are separate, independent modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gnss_monitor.framing import Framer
from gnss_monitor.model import FixQuality, GGAMessage, RMCMessage
from gnss_monitor.parsing import NmeaParser
from gnss_monitor.sources.base import NMEASource


@dataclass
class ReceiverState:
    """Mutable snapshot of what a receiver has reported so far."""

    sentences_seen: int = 0
    valid_checksums: int = 0
    has_fix: bool = False
    fix_quality: Optional[FixQuality] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    altitude_m: Optional[float] = None
    num_satellites: Optional[int] = None
    hdop: Optional[float] = None
    last_fix_utc: Optional[str] = None


class ReceiverMonitor:
    """Drives one receiver's data stream and tracks its latest state."""

    def __init__(
        self,
        receiver_id: str,
        display_name: str,
        source: NMEASource,
        framer: Optional[Framer] = None,
        parser: Optional[NmeaParser] = None,
    ) -> None:
        self.receiver_id = receiver_id
        self.display_name = display_name
        self._source = source
        self._framer = framer or Framer()
        self._parser = parser or NmeaParser()
        self.state = ReceiverState()

    @property
    def source(self) -> NMEASource:
        return self._source

    def poll(self, max_lines: int) -> int:
        """Read up to max_lines from the source, updating state.

        Returns the number of lines actually processed this call. Stops
        early if the source has no more data available right now.
        """
        processed = 0
        for _ in range(max_lines):
            line = self._source.read_line()
            if line is None:
                break
            self._process_line(line)
            processed += 1
        return processed

    def _process_line(self, line: str) -> None:
        sentence = self._framer.frame(line, channel_id=self.receiver_id)
        if sentence is None:
            return
        self.state.sentences_seen += 1
        if sentence.checksum_ok:
            self.state.valid_checksums += 1
        self._apply(self._parser.parse(sentence))

    def _apply(self, message: object) -> None:
        """Update state from a parsed message (latest valid wins)."""
        if isinstance(message, GGAMessage):
            if (
                message.has_fix
                and message.latitude_deg is not None
                and message.longitude_deg is not None
            ):
                self.state.has_fix = True
                self.state.fix_quality = message.fix_quality
                self.state.latitude_deg = message.latitude_deg
                self.state.longitude_deg = message.longitude_deg
                self.state.altitude_m = message.altitude_m
                self.state.num_satellites = message.num_satellites
                self.state.hdop = message.hdop
                if message.utc_time is not None:
                    self.state.last_fix_utc = message.utc_time
        elif isinstance(message, RMCMessage):
            if (
                message.is_valid
                and message.latitude_deg is not None
                and message.longitude_deg is not None
            ):
                self.state.has_fix = True
                self.state.latitude_deg = message.latitude_deg
                self.state.longitude_deg = message.longitude_deg
                if message.utc_time is not None:
                    self.state.last_fix_utc = message.utc_time