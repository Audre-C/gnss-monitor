"""Typed NMEA message classes.

The parser converts an NmeaSentence into one of these dataclasses. Each
class represents the decoded, typed content of one sentence type. Design
rules that later phases depend on:

* Talker-independent: a GGA is a GGAMessage whether it arrived as
  $GPGGA, $GNGGA, or any other talker. The talker is preserved as a
  field, never used to choose the class.
* Missing fields become None, never a crash. Real receivers emit empty
  fields constantly (cold start, no fix), so every numeric field is
  Optional.
* Every message keeps a reference to its originating NmeaSentence, so
  the raw text and receipt metadata remain available downstream.
* Unknown or unparseable sentences become UnknownMessage, preserving the
  raw sentence for future analysis rather than being discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gnss_monitor.model.sentence import NmeaSentence


class FixQuality(Enum):
    """GGA fix-quality indicator (field 6 of a GGA sentence)."""

    INVALID = 0
    GPS = 1
    DGPS = 2
    PPS = 3
    RTK_FIXED = 4
    RTK_FLOAT = 5
    ESTIMATED = 6
    MANUAL = 7
    SIMULATION = 8

    @classmethod
    def from_raw(cls, value: str) -> Optional["FixQuality"]:
        value = value.strip()
        if value == "":
            return None
        try:
            return cls(int(value))
        except (ValueError, KeyError):
            return None


class FixType(Enum):
    """GSA fix-type indicator (field 2 of a GSA sentence)."""

    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3

    @classmethod
    def from_raw(cls, value: str) -> Optional["FixType"]:
        value = value.strip()
        if value == "":
            return None
        try:
            return cls(int(value))
        except (ValueError, KeyError):
            return None


@dataclass(frozen=True, slots=True)
class SatelliteInfo:
    """One satellite entry from a GSV sentence."""

    prn: Optional[int]
    elevation_deg: Optional[int]
    azimuth_deg: Optional[int]
    snr_dbhz: Optional[int]


@dataclass(frozen=True, slots=True)
class BaseMessage:
    """Common fields shared by all parsed messages."""

    sentence: NmeaSentence

    @property
    def talker(self) -> Optional[str]:
        return self.sentence.talker

    @property
    def channel_id(self) -> Optional[str]:
        return self.sentence.channel_id


@dataclass(frozen=True, slots=True)
class GGAMessage(BaseMessage):
    """Global Positioning System Fix Data."""

    utc_time: Optional[str]
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    fix_quality: Optional[FixQuality]
    num_satellites: Optional[int]
    hdop: Optional[float]
    altitude_m: Optional[float]
    geoid_separation_m: Optional[float]

    @property
    def has_fix(self) -> bool:
        return (
            self.fix_quality is not None
            and self.fix_quality is not FixQuality.INVALID
        )


@dataclass(frozen=True, slots=True)
class RMCMessage(BaseMessage):
    """Recommended Minimum Specific GNSS Data."""

    utc_time: Optional[str]
    status: Optional[str]  # "A" = valid, "V" = void
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    speed_knots: Optional[float]
    course_deg: Optional[float]
    date: Optional[str]  # raw ddmmyy field

    @property
    def is_valid(self) -> bool:
        return self.status == "A"

    @property
    def speed_mps(self) -> Optional[float]:
        if self.speed_knots is None:
            return None
        return self.speed_knots * 0.514444


@dataclass(frozen=True, slots=True)
class GSAMessage(BaseMessage):
    """GNSS DOP and Active Satellites."""

    selection_mode: Optional[str]  # "A" auto, "M" manual
    fix_type: Optional[FixType]
    satellite_prns: tuple[int, ...]
    pdop: Optional[float]
    hdop: Optional[float]
    vdop: Optional[float]
    system_id: Optional[int]  # NMEA 4.1 trailing field; None if absent


@dataclass(frozen=True, slots=True)
class GSVMessage(BaseMessage):
    """GNSS Satellites in View."""

    total_messages: Optional[int]
    message_number: Optional[int]
    satellites_in_view: Optional[int]
    satellites: tuple[SatelliteInfo, ...]


@dataclass(frozen=True, slots=True)
class VTGMessage(BaseMessage):
    """Course Over Ground and Ground Speed."""

    course_true_deg: Optional[float]
    course_magnetic_deg: Optional[float]
    speed_knots: Optional[float]
    speed_kph: Optional[float]

    @property
    def speed_mps(self) -> Optional[float]:
        if self.speed_kph is not None:
            return self.speed_kph / 3.6
        if self.speed_knots is not None:
            return self.speed_knots * 0.514444
        return None


@dataclass(frozen=True, slots=True)
class GLLMessage(BaseMessage):
    """Geographic Position - Latitude/Longitude."""

    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    utc_time: Optional[str]
    status: Optional[str]  # "A" valid, "V" void


@dataclass(frozen=True, slots=True)
class TXTMessage(BaseMessage):
    """Text Transmission (informational/status messages)."""

    total_messages: Optional[int]
    message_number: Optional[int]
    text_id: Optional[int]
    text: Optional[str]


@dataclass(frozen=True, slots=True)
class UnknownMessage(BaseMessage):
    """Any sentence that is not recognised or could not be parsed.

    This is never an error state. Proprietary sentences ($PAIR, $PUBX,
    ...), unknown standard types, and sentences with malformed bodies all
    become UnknownMessage so the raw text is preserved and the stream
    keeps flowing. The reason string aids diagnostics.
    """

    reason: str