"""Unit tests for the NMEA parser, driven by the real test_data corpus.

These tests validate parser behaviour against actual captures from the
three receivers (neo6m, lc29hea, neom10). They read from test_data/ so
the corpus can grow with the rooftop installation without touching test
code.
"""

from __future__ import annotations

import pytest

from gnss_monitor.framing import Framer
from gnss_monitor.model import (
    FixQuality,
    FixType,
    GGAMessage,
    GLLMessage,
    GSAMessage,
    GSVMessage,
    RMCMessage,
    TXTMessage,
    UnknownMessage,
    VTGMessage,
)
from gnss_monitor.parsing import NmeaParser
from tests.fixtures import read_lines

RECEIVERS = ["neo6m", "lc29hea", "neom10"]


@pytest.fixture
def framer() -> Framer:
    return Framer()


@pytest.fixture
def parser() -> NmeaParser:
    return NmeaParser()


def parse_all(framer: Framer, parser: NmeaParser, receiver: str):
    """Frame and parse every line of a receiver's normal capture."""
    messages = []
    for line in read_lines(receiver):
        sentence = framer.frame(line)
        assert sentence is not None
        messages.append(parser.parse(sentence))
    return messages


class TestCorpusNeverCrashes:
    @pytest.mark.parametrize("receiver", RECEIVERS)
    def test_every_line_parses_to_a_message(
        self, framer: Framer, parser: NmeaParser, receiver: str
    ) -> None:
        messages = parse_all(framer, parser, receiver)
        assert len(messages) > 0
        # Every result is a message object (never an exception / None).
        assert all(m is not None for m in messages)

    @pytest.mark.parametrize("receiver", RECEIVERS)
    def test_corpus_contains_recognised_sentences(
        self, framer: Framer, parser: NmeaParser, receiver: str
    ) -> None:
        messages = parse_all(framer, parser, receiver)
        recognised = [
            m for m in messages if not isinstance(m, UnknownMessage)
        ]
        # Each real capture must contain some recognised navigation data.
        assert len(recognised) > 0


class TestGGA:
    def test_neom10_first_gga_has_fix(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        # neom10/normal.nmea begins already fixed near 25.33N, 51.47E.
        ggas = [
            m
            for m in parse_all(framer, parser, "neom10")
            if isinstance(m, GGAMessage)
        ]
        fixed = [g for g in ggas if g.has_fix]
        assert fixed, "expected at least one fixed GGA in neom10 corpus"
        g = fixed[0]
        assert g.fix_quality == FixQuality.GPS
        assert g.latitude_deg is not None and 25.0 < g.latitude_deg < 26.0
        assert (
            g.longitude_deg is not None and 51.0 < g.longitude_deg < 52.0
        )
        assert g.num_satellites is not None and g.num_satellites > 0
        assert g.hdop is not None

    def test_no_fix_gga_has_none_position(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        # A cold-start GGA with empty position must yield None lat/lon
        # and INVALID fix, not an exception.
        s = framer.frame("$GPGGA,,,,,,0,00,99.99,,,,,,*48")
        assert s is not None
        m = parser.parse(s)
        assert isinstance(m, GGAMessage)
        assert m.latitude_deg is None
        assert m.longitude_deg is None
        assert m.fix_quality == FixQuality.INVALID
        assert m.has_fix is False

    def test_talker_independence_gp_vs_gn(
        self, parser: NmeaParser
    ) -> None:
        framer = Framer()
        gp = parser.parse(
            framer.frame(
                "$GPGGA,112724.00,2520.02526,N,05128.15637,E,1,03,"
                "5.86,-0.5,M,-24.7,M,,*5D"
            )
        )
        gn = parser.parse(
            framer.frame(
                "$GNGGA,113954.00,2520.06189,N,05128.16076,E,1,08,"
                "1.41,39.1,M,-24.7,M,,*59"
            )
        )
        assert isinstance(gp, GGAMessage)
        assert isinstance(gn, GGAMessage)
        # Both decode to the same class regardless of talker.
        assert gp.talker == "GP"
        assert gn.talker == "GN"


class TestCoordinateConversion:
    def test_latitude_conversion(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        # 2520.06189,N -> 25 + 20.06189/60 = 25.334365 deg
        s = framer.frame(
            "$GNGGA,113954.00,2520.06189,N,05128.16076,E,1,08,1.41,"
            "39.1,M,-24.7,M,,*59"
        )
        m = parser.parse(s)
        assert isinstance(m, GGAMessage)
        assert m.latitude_deg == pytest.approx(25.334365, abs=1e-5)

    def test_longitude_conversion(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        # 05128.16076,E -> 51 + 28.16076/60 = 51.469346 deg
        s = framer.frame(
            "$GNGGA,113954.00,2520.06189,N,05128.16076,E,1,08,1.41,"
            "39.1,M,-24.7,M,,*59"
        )
        m = parser.parse(s)
        assert isinstance(m, GGAMessage)
        assert m.longitude_deg == pytest.approx(51.469346, abs=1e-5)

    def test_southern_western_hemisphere_signs(
        self, parser: NmeaParser
    ) -> None:
        framer = Framer()
        # Synthetic S/W to confirm sign handling (checksum recomputed).
        from gnss_monitor.framing import compute_checksum

        body = "GNGGA,120000.00,2520.00000,S,05128.00000,W,1,05,1.0,10,M,0,M,,"
        line = f"${body}*{compute_checksum(body):02X}"
        m = parser.parse(framer.frame(line))
        assert isinstance(m, GGAMessage)
        assert m.latitude_deg is not None and m.latitude_deg < 0
        assert m.longitude_deg is not None and m.longitude_deg < 0


class TestRMC:
    def test_void_status_before_fix(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame("$GPRMC,,V,,,,,,,,,,N*53")
        m = parser.parse(s)
        assert isinstance(m, RMCMessage)
        assert m.status == "V"
        assert m.is_valid is False
        assert m.latitude_deg is None

    def test_valid_status_and_speed(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame(
            "$GNRMC,113954.00,A,2520.06189,N,05128.16076,E,0.011,,"
            "200726,,,A,V*15"
        )
        m = parser.parse(s)
        assert isinstance(m, RMCMessage)
        assert m.is_valid is True
        assert m.speed_knots == pytest.approx(0.011)
        assert m.speed_mps is not None
        assert m.date == "200726"


class TestGSA:
    def test_gsa_no_fix(self, framer: Framer, parser: NmeaParser) -> None:
        s = framer.frame(
            "$GNGSA,A,1,,,,,,,,,,,,,99.99,99.99,99.99,1*33"
        )
        m = parser.parse(s)
        assert isinstance(m, GSAMessage)
        assert m.fix_type == FixType.NO_FIX
        assert m.satellite_prns == tuple()
        assert m.system_id == 1

    def test_gsa_3d_fix_collects_prns(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame(
            "$GNGSA,A,3,16,31,04,26,09,,,,,,,,3.89,1.41,3.63,1*0D"
        )
        m = parser.parse(s)
        assert isinstance(m, GSAMessage)
        assert m.fix_type == FixType.FIX_3D
        assert m.satellite_prns == (16, 31, 4, 26, 9)
        assert m.pdop == pytest.approx(3.89)
        assert m.hdop == pytest.approx(1.41)
        assert m.vdop == pytest.approx(3.63)

    def test_gsa_without_trailing_system_id(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        # NEO-6M GSA has no NMEA 4.1 trailing system id field.
        s = framer.frame(
            "$GPGSA,A,1,31,28,03,,,,,,,,,,5.94,5.86,1.00*09"
        )
        m = parser.parse(s)
        assert isinstance(m, GSAMessage)
        assert m.system_id is None
        assert m.satellite_prns == (31, 28, 3)


class TestGSV:
    def test_gsv_empty(self, framer: Framer, parser: NmeaParser) -> None:
        s = framer.frame("$GPGSV,1,1,00*79")
        m = parser.parse(s)
        assert isinstance(m, GSVMessage)
        assert m.satellites_in_view == 0
        assert m.satellites == tuple()

    def test_gsv_parses_satellites(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame(
            "$GPGSV,2,1,08,04,42,298,34,09,14,275,31,16,22,134,36,"
            "26,32,101,34,1*61"
        )
        m = parser.parse(s)
        assert isinstance(m, GSVMessage)
        assert m.total_messages == 2
        assert m.message_number == 1
        assert m.satellites_in_view == 8
        # Four satellites in this first message.
        assert len(m.satellites) == 4
        first = m.satellites[0]
        assert first.prn == 4
        assert first.elevation_deg == 42
        assert first.azimuth_deg == 298
        assert first.snr_dbhz == 34


class TestVTG:
    def test_vtg_speed(self, framer: Framer, parser: NmeaParser) -> None:
        s = framer.frame("$GNVTG,,T,,M,0.011,N,0.021,K,A*3E")
        m = parser.parse(s)
        assert isinstance(m, VTGMessage)
        assert m.speed_knots == pytest.approx(0.011)
        assert m.speed_kph == pytest.approx(0.021)
        assert m.speed_mps == pytest.approx(0.021 / 3.6)


class TestGLL:
    def test_gll_void(self, framer: Framer, parser: NmeaParser) -> None:
        s = framer.frame("$GPGLL,,,,,112723.00,V,N*4E")
        m = parser.parse(s)
        assert isinstance(m, GLLMessage)
        assert m.status == "V"
        assert m.latitude_deg is None


class TestTXT:
    def test_txt_message(self, framer: Framer, parser: NmeaParser) -> None:
        s = framer.frame("$GPTXT,01,01,01,PQTM inv format*24")
        m = parser.parse(s)
        assert isinstance(m, TXTMessage)
        assert m.text == "PQTM inv format"
        assert m.total_messages == 1


class TestProprietaryAndUnknown:
    def test_proprietary_pair_is_unknown_preserved(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame("$PAIR010,0,0,2428,128198*3C")
        m = parser.parse(s)
        assert isinstance(m, UnknownMessage)
        assert "proprietary" in m.reason
        # Raw sentence preserved for future analysis.
        assert m.sentence.raw == "$PAIR010,0,0,2428,128198*3C"

    def test_unsupported_type_is_unknown(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        from gnss_monitor.framing import compute_checksum

        body = "GNZZZ,1,2,3"
        line = f"${body}*{compute_checksum(body):02X}"
        m = parser.parse(framer.frame(line))
        assert isinstance(m, UnknownMessage)
        assert "unsupported" in m.reason

    def test_truncated_line_is_unknown(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame("$GNGL")
        m = parser.parse(s)
        assert isinstance(m, UnknownMessage)

    def test_non_nmea_line_is_unknown(
        self, framer: Framer, parser: NmeaParser
    ) -> None:
        s = framer.frame("random noise")
        m = parser.parse(s)
        assert isinstance(m, UnknownMessage)