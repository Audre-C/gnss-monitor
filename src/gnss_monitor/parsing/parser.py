"""NMEA parser: NmeaSentence -> typed message objects.

The parser dispatches strictly on sentence TYPE (GGA, RMC, GSV, ...) and
never on the talker ID. A GGA is decoded identically whether it arrived
as $GPGGA, $GNGGA, $GBGGA, or any other talker. This is essential for the
multi-constellation receivers, which mix GP/GN/GL/GA/GB/GQ freely.

Robustness guarantees:
    * Never raises on bad input. Any sentence that cannot be decoded
      becomes an UnknownMessage with a diagnostic reason, and the raw
      sentence is preserved.
    * Missing/empty fields yield None, not exceptions.
    * Proprietary sentences ($PAIR, $PUBX, ...) and unrecognised types
      become UnknownMessage, preserving them for future analysis.

The parser has no knowledge of where sentences came from (file, serial,
network). It only consumes NmeaSentence objects.
"""

from __future__ import annotations

from typing import Callable, Optional

from gnss_monitor.model.messages import (
    BaseMessage,
    FixQuality,
    FixType,
    GGAMessage,
    GLLMessage,
    GSAMessage,
    GSVMessage,
    RMCMessage,
    SatelliteInfo,
    TXTMessage,
    UnknownMessage,
    VTGMessage,
)
from gnss_monitor.model.sentence import NmeaSentence


# ---------------------------------------------------------------------------
# Low-level field converters (all tolerant of empty/malformed input)
# ---------------------------------------------------------------------------


def _to_float(value: str) -> Optional[float]:
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str) -> Optional[int]:
    value = value.strip()
    if value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _clean_str(value: str) -> Optional[str]:
    value = value.strip()
    return value if value != "" else None


def _nmea_coord_to_degrees(
    coord: str, hemisphere: str
) -> Optional[float]:
    """Convert an NMEA ddmm.mmmm / dddmm.mmmm coordinate to decimal degrees.

    Latitude uses 2 degree digits, longitude uses 3. The number of degree
    digits is inferred from the position of the decimal point, which is
    robust to either case:

        2520.06189  -> 25 deg + 20.06189 min
        05128.16076 -> 51 deg + 28.16076 min

    Returns None if the field is empty or unparseable. The hemisphere
    (N/S/E/W) sets the sign.
    """
    coord = coord.strip()
    hemisphere = hemisphere.strip().upper()
    if coord == "":
        return None

    dot = coord.find(".")
    # Minutes are always the two digits immediately left of the decimal
    # point (plus the fractional part); everything before that is degrees.
    if dot == -1:
        # No decimal point: fall back to "last two digits are minutes".
        if len(coord) < 3:
            return None
        split_at = len(coord) - 2
    else:
        split_at = dot - 2

    if split_at < 0:
        return None

    deg_part = coord[:split_at]
    min_part = coord[split_at:]

    try:
        degrees = int(deg_part) if deg_part != "" else 0
        minutes = float(min_part)
    except ValueError:
        return None

    value = degrees + minutes / 60.0

    if hemisphere in ("S", "W"):
        value = -value
    elif hemisphere not in ("N", "E"):
        # Unknown hemisphere character -> cannot trust the sign.
        return None

    return value


def _field(fields: tuple[str, ...], index: int) -> str:
    """Return field at index, or empty string if out of range."""
    return fields[index] if index < len(fields) else ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class NmeaParser:
    """Converts NmeaSentence objects into typed message objects."""

    def __init__(self) -> None:
        self._handlers: dict[
            str, Callable[[NmeaSentence], BaseMessage]
        ] = {
            "GGA": self._parse_gga,
            "RMC": self._parse_rmc,
            "GSA": self._parse_gsa,
            "GSV": self._parse_gsv,
            "VTG": self._parse_vtg,
            "GLL": self._parse_gll,
            "TXT": self._parse_txt,
        }

    def parse(self, sentence: NmeaSentence) -> BaseMessage:
        """Parse one sentence into a typed message.

        Always returns a message object; unrecognised or unparseable
        input becomes UnknownMessage. Never raises.
        """
        sentence_type = sentence.sentence_type

        if sentence_type is None:
            return UnknownMessage(
                sentence=sentence,
                reason="no sentence type (malformed or non-NMEA line)",
            )

        if sentence.talker is None:
            # Proprietary sentence ($PAIR, $PUBX, ...). Preserve it.
            return UnknownMessage(
                sentence=sentence,
                reason=f"proprietary sentence '{sentence_type}'",
            )

        handler = self._handlers.get(sentence_type)
        if handler is None:
            return UnknownMessage(
                sentence=sentence,
                reason=f"unsupported sentence type '{sentence_type}'",
            )

        try:
            return handler(sentence)
        except Exception as exc:  # noqa: BLE001 - robustness by design
            # A parser bug or an unexpected field layout must never take
            # down the stream. Preserve the raw sentence for diagnosis.
            return UnknownMessage(
                sentence=sentence,
                reason=f"parse error in {sentence_type}: {exc!r}",
            )

    # -- individual sentence handlers ---------------------------------------

    @staticmethod
    def _parse_gga(s: NmeaSentence) -> GGAMessage:
        f = s.fields
        return GGAMessage(
            sentence=s,
            utc_time=_clean_str(_field(f, 0)),
            latitude_deg=_nmea_coord_to_degrees(
                _field(f, 1), _field(f, 2)
            ),
            longitude_deg=_nmea_coord_to_degrees(
                _field(f, 3), _field(f, 4)
            ),
            fix_quality=FixQuality.from_raw(_field(f, 5)),
            num_satellites=_to_int(_field(f, 6)),
            hdop=_to_float(_field(f, 7)),
            altitude_m=_to_float(_field(f, 8)),
            geoid_separation_m=_to_float(_field(f, 10)),
        )

    @staticmethod
    def _parse_rmc(s: NmeaSentence) -> RMCMessage:
        f = s.fields
        return RMCMessage(
            sentence=s,
            utc_time=_clean_str(_field(f, 0)),
            status=_clean_str(_field(f, 1)),
            latitude_deg=_nmea_coord_to_degrees(
                _field(f, 2), _field(f, 3)
            ),
            longitude_deg=_nmea_coord_to_degrees(
                _field(f, 4), _field(f, 5)
            ),
            speed_knots=_to_float(_field(f, 6)),
            course_deg=_to_float(_field(f, 7)),
            date=_clean_str(_field(f, 8)),
        )

    @staticmethod
    def _parse_gsa(s: NmeaSentence) -> GSAMessage:
        f = s.fields
        # Fields: mode, fixtype, then up to 12 satellite PRNs, then
        # PDOP, HDOP, VDOP, and optionally a trailing system id.
        prn_fields = f[2:14]
        prns = tuple(
            prn for prn in (_to_int(x) for x in prn_fields) if prn is not None
        )
        pdop = _to_float(_field(f, 14))
        hdop = _to_float(_field(f, 15))
        vdop = _to_float(_field(f, 16))
        system_id = _to_int(_field(f, 17)) if len(f) > 17 else None
        return GSAMessage(
            sentence=s,
            selection_mode=_clean_str(_field(f, 0)),
            fix_type=FixType.from_raw(_field(f, 1)),
            satellite_prns=prns,
            pdop=pdop,
            hdop=hdop,
            vdop=vdop,
            system_id=system_id,
        )

    @staticmethod
    def _parse_gsv(s: NmeaSentence) -> GSVMessage:
        f = s.fields
        total_messages = _to_int(_field(f, 0))
        message_number = _to_int(_field(f, 1))
        satellites_in_view = _to_int(_field(f, 2))

        # Satellite blocks start at field index 3, four fields each:
        # PRN, elevation, azimuth, SNR. Some receivers append a trailing
        # signal-id field after the last block; blocks that fall short are
        # skipped rather than raising.
        satellites: list[SatelliteInfo] = []
        index = 3
        while index + 3 < len(f) + 1 and index + 0 < len(f):
            prn = _to_int(_field(f, index))
            elevation = _to_int(_field(f, index + 1))
            azimuth = _to_int(_field(f, index + 2))
            snr = _to_int(_field(f, index + 3))
            # A block with no PRN at all is treated as padding/trailing
            # data (e.g. a lone signal-id field) and skipped.
            if (
                prn is None
                and elevation is None
                and azimuth is None
                and snr is None
            ):
                index += 4
                continue
            satellites.append(
                SatelliteInfo(
                    prn=prn,
                    elevation_deg=elevation,
                    azimuth_deg=azimuth,
                    snr_dbhz=snr,
                )
            )
            index += 4

        return GSVMessage(
            sentence=s,
            total_messages=total_messages,
            message_number=message_number,
            satellites_in_view=satellites_in_view,
            satellites=tuple(satellites),
        )

    @staticmethod
    def _parse_vtg(s: NmeaSentence) -> VTGMessage:
        f = s.fields
        # Fields: course_true, T, course_mag, M, speed_kn, N, speed_kph, K
        return VTGMessage(
            sentence=s,
            course_true_deg=_to_float(_field(f, 0)),
            course_magnetic_deg=_to_float(_field(f, 2)),
            speed_knots=_to_float(_field(f, 4)),
            speed_kph=_to_float(_field(f, 6)),
        )

    @staticmethod
    def _parse_gll(s: NmeaSentence) -> GLLMessage:
        f = s.fields
        return GLLMessage(
            sentence=s,
            latitude_deg=_nmea_coord_to_degrees(
                _field(f, 0), _field(f, 1)
            ),
            longitude_deg=_nmea_coord_to_degrees(
                _field(f, 2), _field(f, 3)
            ),
            utc_time=_clean_str(_field(f, 4)),
            status=_clean_str(_field(f, 5)),
        )

    @staticmethod
    def _parse_txt(s: NmeaSentence) -> TXTMessage:
        f = s.fields
        return TXTMessage(
            sentence=s,
            total_messages=_to_int(_field(f, 0)),
            message_number=_to_int(_field(f, 1)),
            text_id=_to_int(_field(f, 2)),
            text=_clean_str(_field(f, 3)),
        )