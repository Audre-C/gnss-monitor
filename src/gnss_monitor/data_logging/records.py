"""Transport records for the data-logging subsystem.

Each record type is a small, frozen, plain-data carrier for exactly one
row that will eventually land in a file. They intentionally do not know
how to format or write themselves (see writers.py) and carry no behavior
- they only exist so that the fast, non-blocking "observe something
happened" call (in ReceiverMonitor or LiveController) and the slow,
disk-touching write (on the background thread) can be decoupled by a
queue. Adding a future data type (UBX, RTCM, Teleport events, external
alarms) means adding one more record class here plus a matching writer
in writers.py; the queue/thread plumbing in logger.py does not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class RawSentenceRecord:
    """One verbatim NMEA sentence, exactly as received on the wire."""

    receiver_id: str
    t_wall: float
    raw: str


@dataclass(frozen=True, slots=True)
class ParsedMessageRecord:
    """The decoded interpretation of one NMEA sentence.

    avg_cn0_dbhz/analysis_score/analysis_state are the most recently
    known values at the time this sentence was parsed, not necessarily
    computed from this exact sentence - see DataLogger.update_receiver_
    context(). They are None whenever analysis is not configured or no
    value has been computed yet.
    """

    receiver_id: str
    t_wall: float
    sentence_type: Optional[str]
    talker: Optional[str]
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    has_fix: Optional[bool]
    num_satellites: Optional[int]
    hdop: Optional[float]
    speed_mps: Optional[float]
    avg_cn0_dbhz: Optional[float]
    analysis_score: Optional[float]
    analysis_state: Optional[str]


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """One receiver's full state at one instant, on a fixed interval.

    Combines acquisition (position/fix/HDOP/...) and analysis (score/
    triggered rules) on a single timestamp, which is what makes this the
    most convenient dataset for offline tooling - no need to join two
    files by nearest timestamp.
    """

    receiver_id: str
    t_wall: float
    name: str
    constellation: str
    health: str
    analysis_state: Optional[str]
    score: Optional[float]
    latitude_deg: Optional[float]
    longitude_deg: Optional[float]
    distance_from_expected_m: Optional[float]
    speed_mps: Optional[float]
    has_fix: Optional[bool]
    hdop: Optional[float]
    num_satellites: Optional[int]
    avg_cn0_dbhz: Optional[float]
    triggered_rules: str
