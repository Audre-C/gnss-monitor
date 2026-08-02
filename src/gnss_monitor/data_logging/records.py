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

    sentence_utc_time is this exact sentence's own UTC field (GGA/RMC
    only; None otherwise), independent of fix validity - unlike
    ReceiverState.last_fix_utc, which only updates on a valid fix, this
    is the receiver's raw clock reading whether or not it currently has
    a fix. It is the receiver-side half of a clock-drift check: diff it
    against `timestamp` (the Pi's wall clock) to detect Pi clock drift
    or a clock reset without having to grep the raw NMEA archive by hand.
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
    sentence_utc_time: Optional[str]
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

    last_fix_utc mirrors ReceiverState.last_fix_utc: the UTC field from
    the receiver's last valid fix, independent of the Pi's own wall
    clock. Same clock-drift use as ParsedMessageRecord.sentence_utc_time
    (diff it against `timestamp`), just sampled once per interval tick
    instead of once per sentence.
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
    last_fix_utc: Optional[str]
    triggered_rules: str
