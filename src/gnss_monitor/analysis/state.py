"""Per-receiver rolling state the scoring engine needs beyond one instant.

Several indicators are inherently about *change* rather than a single
reading: a satellite-count "sudden drop", a timestamp "jump", a speed
reading while the platform is expected to be stationary. This module is
the analysis package's own small memory of the previous sample per
receiver - deliberately separate from ReceiverState
(gnss_monitor.monitor.receiver_monitor): the acquisition layer's job is
"what is the latest known state"; delta-based scoring is an analysis
concern, not an acquisition one.

ReceiverSample is also the analysis package's entire input contract.
Anything - live ReceiverState, a replayed message, a test fixture - that
can populate a ReceiverSample can be scored, without the analysis
package needing to know where the data came from. This is what lets it
be wired in later (Simple Mode's ReceiverState today, something else
tomorrow) without refactoring the scoring logic itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True, slots=True)
class ReceiverSample:
    """One receiver's instantaneous, decoded state at evaluation time.

    All fields are optional because real receivers omit fields
    constantly (cold start, no fix, a sentence type not yet seen).
    utc_time_s is seconds-since-midnight-UTC (0-86400), already parsed
    from whatever raw representation the source used - see
    parse_nmea_utc_seconds() for the standard NMEA "hhmmss.ss" format.
    """

    has_fix: bool = False
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    num_satellites: Optional[int] = None
    hdop: Optional[float] = None
    speed_mps: Optional[float] = None
    utc_time_s: Optional[float] = None


@dataclass
class ReceiverHistory:
    """The current and immediately-previous sample for one receiver."""

    current: Optional[ReceiverSample] = None
    previous: Optional[ReceiverSample] = None

    def push(self, sample: ReceiverSample) -> None:
        """Record `sample` as current, demoting the old current to previous."""
        self.previous = self.current
        self.current = sample


def parse_nmea_utc_seconds(raw: Optional[str]) -> Optional[float]:
    """Parse an NMEA 'hhmmss.ss' UTC time field into seconds-since-midnight.

    Returns None for missing or malformed input rather than raising,
    matching how every other optional NMEA field is treated upstream
    (see gnss_monitor.parsing.parser).
    """
    if not raw:
        return None
    try:
        hours = int(raw[0:2])
        minutes = int(raw[2:4])
        seconds = float(raw[4:])
    except (ValueError, IndexError):
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def shortest_time_delta_s(current_s: float, previous_s: float) -> float:
    """Signed gap between two seconds-since-midnight values.

    Normalizes midnight rollover (e.g. 23:59:59 -> 00:00:01 is a +2s
    step, not a -86398s one) to the shortest signed distance around the
    24-hour clock.
    """
    delta = current_s - previous_s
    if delta > _SECONDS_PER_DAY / 2:
        delta -= _SECONDS_PER_DAY
    elif delta < -_SECONDS_PER_DAY / 2:
        delta += _SECONDS_PER_DAY
    return delta
