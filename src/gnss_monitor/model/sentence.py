"""Raw NMEA sentence container.

An NmeaSentence is the boundary object between the acquisition layer
(sources) and the parsing layer. It carries the verbatim sentence text
together with the metadata every downstream stage needs, regardless of
whether the bytes came from a file, a serial port, or a future network
stream.

The framer produces these objects. Nothing in this module knows how the
raw text was obtained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True, slots=True)
class NmeaSentence:
    """A single raw NMEA sentence with receipt metadata.

    Attributes:
        raw:
            The verbatim sentence as received, without the trailing
            CR/LF (e.g. "$GPGGA,...,*48"). Preserved exactly so it can
            be written to the raw archive and re-parsed by future
            analysers.
        checksum_ok:
            True if the sentence had a valid "*hh" checksum that matched
            the XOR of the body. False if the checksum was present but
            wrong. None if no checksum field was present at all.
        talker:
            The two-character talker ID (e.g. "GP", "GN", "GL", "GA",
            "GB", "GQ", "BD") when the sentence follows the standard
            "$ttSSS" form. None for proprietary ("$P...") or malformed
            sentences.
        sentence_type:
            The three-character sentence type (e.g. "GGA", "RMC", "GSV")
            for standard sentences. For proprietary sentences this holds
            the proprietary identifier (e.g. "PAIR010", "PUBX"). None if
            it could not be determined.
        fields:
            The comma-separated fields of the sentence body, excluding
            the leading address field and the trailing checksum. Empty
            fields are preserved as empty strings so positional parsing
            downstream stays correct.
        channel_id:
            Identifier of the receiver/channel this sentence came from.
            Set by the channel in later phases; may be None when a
            sentence is framed in isolation (e.g. in unit tests).
        t_rx_wall:
            Wall-clock receipt time (Unix epoch seconds) or None if not
            recorded. Populated by sources in later phases.
        t_rx_mono:
            Monotonic receipt timestamp (seconds) or None. Used for
            latency/interval measurements that must be immune to system
            clock changes.
    """

    raw: str
    checksum_ok: Optional[bool]
    talker: Optional[str]
    sentence_type: Optional[str]
    fields: tuple[str, ...] = field(default_factory=tuple)
    channel_id: Optional[str] = None
    t_rx_wall: Optional[float] = None
    t_rx_mono: Optional[float] = None

    @property
    def is_proprietary(self) -> bool:
        """True if this is a proprietary ("$P...") sentence."""
        return self.raw.startswith("$P")

    @property
    def address(self) -> Optional[str]:
        """The full address field without the leading '$'.

        For "$GPGGA,..." this returns "GPGGA". For "$PAIR010,..." this
        returns "PAIR010". None if the sentence does not start with '$'
        or has no address field.
        """
        if not self.raw.startswith("$"):
            return None
        body = self.raw[1:]
        head = body.split(",", 1)[0]
        head = head.split("*", 1)[0]
        return head or None