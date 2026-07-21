"""NMEA framing: raw lines to validated NmeaSentence objects."""

from gnss_monitor.framing.framer import Framer, compute_checksum

__all__ = ["Framer", "compute_checksum"]