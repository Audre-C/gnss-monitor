"""Unit tests for the NMEA framer, driven by the real test_data corpus."""

from __future__ import annotations

import pytest

from gnss_monitor.framing import Framer, compute_checksum
from tests.fixtures import read_lines

RECEIVERS = ["neo6m", "lc29hea", "neom10"]


@pytest.fixture
def framer() -> Framer:
    return Framer()


class TestChecksum:
    def test_known_checksum(self) -> None:
        # $GPGGA,,,,,,0,00,99.99,,,,,,*48 -> body checksum is 0x48
        body = "GPGGA,,,,,,0,00,99.99,,,,,,"
        assert compute_checksum(body) == 0x48

    def test_all_corpus_lines_have_valid_checksums(
        self, framer: Framer
    ) -> None:
        # Every line we captured from real hardware should checksum OK,
        # except an intentionally-preserved truncated final line, if any.
        for receiver in RECEIVERS:
            for line in read_lines(receiver):
                sentence = framer.frame(line)
                assert sentence is not None
                # A line with a '*hh' must validate; a truncated line
                # without a checksum is allowed to be None.
                if "*" in line:
                    assert sentence.checksum_ok is True, line


class TestFramingStructure:
    def test_blank_line_returns_none(self, framer: Framer) -> None:
        assert framer.frame("") is None
        assert framer.frame("   \r\n") is None

    def test_standard_sentence_splits_talker_and_type(
        self, framer: Framer
    ) -> None:
        s = framer.frame("$GPGGA,,,,,,0,00,99.99,,,,,,*48")
        assert s is not None
        assert s.talker == "GP"
        assert s.sentence_type == "GGA"
        assert s.checksum_ok is True

    def test_gn_talker_recognised(self, framer: Framer) -> None:
        s = framer.frame(
            "$GNRMC,113954.00,A,2520.06189,N,05128.16076,E,"
            "0.011,,200726,,,A,V*15"
        )
        assert s is not None
        assert s.talker == "GN"
        assert s.sentence_type == "RMC"
        assert s.checksum_ok is True

    @pytest.mark.parametrize(
        "talker",
        ["GP", "GN", "GL", "GA", "GB", "GQ"],
    )
    def test_all_talker_ids_split(
        self, framer: Framer, talker: str
    ) -> None:
        body = f"{talker}GSV,1,1,00"
        checksum = compute_checksum(body)
        line = f"${body}*{checksum:02X}"
        s = framer.frame(line)
        assert s is not None
        assert s.talker == talker
        assert s.sentence_type == "GSV"
        assert s.checksum_ok is True

    def test_proprietary_has_no_talker(self, framer: Framer) -> None:
        s = framer.frame("$PAIR010,0,0,2428,128198*3C")
        assert s is not None
        assert s.is_proprietary is True
        assert s.talker is None
        assert s.sentence_type == "PAIR010"
        assert s.checksum_ok is True

    def test_fields_preserve_empty_positions(
        self, framer: Framer
    ) -> None:
        s = framer.frame("$GPRMC,,V,,,,,,,,,,N*53")
        assert s is not None
        # Positional fields must be preserved, including empties.
        assert s.fields[1] == "V"
        assert s.fields[0] == ""


class TestFramingRobustness:
    def test_bad_checksum_flagged_not_raised(
        self, framer: Framer
    ) -> None:
        s = framer.frame("$GPGGA,,,,,,0,00,99.99,,,,,,*00")
        assert s is not None
        assert s.checksum_ok is False
        # Still framed: talker/type available for diagnostics.
        assert s.talker == "GP"

    def test_missing_checksum_is_none(self, framer: Framer) -> None:
        s = framer.frame("$GPGGA,,,,,,0,00,99.99,,,,,,")
        assert s is not None
        assert s.checksum_ok is None
        assert s.talker == "GP"

    def test_truncated_line_survives(self, framer: Framer) -> None:
        # Real artifact: capture stopped mid-sentence ("$GNGL").
        s = framer.frame("$GNGL")
        assert s is not None
        assert s.checksum_ok is None
        # Address too short to be standard -> preserved as type, no talker.
        assert s.talker is None

    def test_non_nmea_line_preserved(self, framer: Framer) -> None:
        s = framer.frame("garbage line not nmea")
        assert s is not None
        assert s.raw == "garbage line not nmea"
        assert s.talker is None
        assert s.sentence_type is None
        assert s.checksum_ok is None

    def test_binary_noise_does_not_raise(self, framer: Framer) -> None:
        s = framer.frame("$\x00\x01\x02*ZZ")
        assert s is not None
        # Checksum field is not valid hex -> False, no exception.
        assert s.checksum_ok is False