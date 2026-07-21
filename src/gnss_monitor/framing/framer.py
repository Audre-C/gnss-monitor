"""NMEA framing and checksum validation.

The framer takes raw text lines (as delivered by a source) and turns each
into an NmeaSentence. It is intentionally forgiving: real receiver output
contains cold-start noise, truncated final lines, proprietary sentences,
and occasional corruption. The framer never raises on bad input; it
records what it found (including checksum failures) and lets the pipeline
continue.

Responsibilities:
    * Strip line terminators and surrounding whitespace.
    * Verify the "$...*hh" structure.
    * Compute and compare the XOR checksum when present.
    * Split the body into the talker, sentence type, and fields.

It does NOT interpret field values; that is the parser's job.
"""

from __future__ import annotations

import re
from typing import Optional

from gnss_monitor.model.sentence import NmeaSentence

# Standard address is a 2-char talker + 3-char sentence type, e.g. GPGGA.
_STANDARD_ADDRESS = re.compile(r"^[A-Za-z]{2}[A-Za-z]{3}$")
_HEX_PAIR = re.compile(r"^[0-9A-Fa-f]{2}$")


def compute_checksum(body: str) -> int:
    """Return the NMEA XOR checksum of a sentence body.

    The body is the text between the leading '$' and the '*', exclusive
    of both.
    """
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return checksum


class Framer:
    """Converts raw lines into NmeaSentence objects.

    Stateless with respect to sentence content; a single instance can be
    reused across channels, though in practice each channel owns its own.
    """

    def frame(
        self,
        line: str,
        channel_id: Optional[str] = None,
        t_rx_wall: Optional[float] = None,
        t_rx_mono: Optional[float] = None,
    ) -> Optional[NmeaSentence]:
        """Frame a single raw line.

        Returns an NmeaSentence, or None if the line is empty/blank after
        stripping (nothing meaningful to represent). A non-None result may
        still have checksum_ok is False or None, and talker/sentence_type
        may be None for malformed or proprietary sentences.
        """
        raw = line.strip()
        if raw == "":
            return None

        # Must start with '$' to be a candidate NMEA sentence. Anything
        # else is preserved as raw with no talker/type and unknown
        # checksum, so the pipeline can still count it.
        if not raw.startswith("$"):
            return NmeaSentence(
                raw=raw,
                checksum_ok=None,
                talker=None,
                sentence_type=None,
                fields=tuple(),
                channel_id=channel_id,
                t_rx_wall=t_rx_wall,
                t_rx_mono=t_rx_mono,
            )

        checksum_ok = self._verify_checksum(raw)
        talker, sentence_type, fields = self._split(raw)

        return NmeaSentence(
            raw=raw,
            checksum_ok=checksum_ok,
            talker=talker,
            sentence_type=sentence_type,
            fields=fields,
            channel_id=channel_id,
            t_rx_wall=t_rx_wall,
            t_rx_mono=t_rx_mono,
        )

    @staticmethod
    def _verify_checksum(raw: str) -> Optional[bool]:
        """Return True/False if a checksum is present, None if absent."""
        star = raw.rfind("*")
        if star == -1:
            return None
        # Body is between '$' and '*'.
        body = raw[1:star]
        given = raw[star + 1 :]
        if not _HEX_PAIR.match(given[:2]) or len(given) < 2:
            return False
        return compute_checksum(body) == int(given[:2], 16)

    @staticmethod
    def _split(
        raw: str,
    ) -> tuple[Optional[str], Optional[str], tuple[str, ...]]:
        """Split a '$'-prefixed sentence into talker, type, and fields.

        For a standard address like GPGGA this yields ("GP", "GGA",
        (field, ...)). For a proprietary address like PAIR010 this yields
        (None, "PAIR010", (field, ...)) so the identifier is preserved
        while signalling (via talker=None) that it is non-standard.
        """
        star = raw.rfind("*")
        content = raw[1:star] if star != -1 else raw[1:]

        parts = content.split(",")
        address = parts[0]
        fields = tuple(parts[1:])

        if _STANDARD_ADDRESS.match(address):
            talker = address[:2].upper()
            sentence_type = address[2:].upper()
            return talker, sentence_type, fields

        # Proprietary or non-standard address: keep the whole address as
        # the "type" for diagnostics, but no talker.
        return None, address, fields