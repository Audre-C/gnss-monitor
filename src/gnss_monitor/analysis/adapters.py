"""Translate acquisition-layer state into the analysis package's input contract.

This is the one place that is allowed to know about ReceiverState
(gnss_monitor.monitor.receiver_monitor): everything else in the analysis
package only ever sees a ReceiverSample (see state.py) and has no idea
where it came from. Keeping the translation in a single small function
is what let the scoring engine itself (rules.py, score.py, evaluator.py)
be built and fully unit-tested before any real integration existed - and
is now what makes that integration a few lines in live/controller.py
instead of a refactor.
"""

from __future__ import annotations

import time

from gnss_monitor.analysis.state import ReceiverSample, parse_nmea_utc_seconds
from gnss_monitor.monitor.receiver_monitor import ReceiverState


def receiver_sample_from_state(state: ReceiverState) -> ReceiverSample:
    """Build a ReceiverSample from a ReceiverMonitor's latest ReceiverState."""
    return ReceiverSample(
        has_fix=state.has_fix,
        latitude_deg=state.latitude_deg,
        longitude_deg=state.longitude_deg,
        num_satellites=state.num_satellites,
        hdop=state.hdop,
        speed_mps=state.speed_mps,
        utc_time_s=parse_nmea_utc_seconds(state.last_fix_utc),
        cn0_dbhz=state.tracked_cn0_dbhz(time.monotonic()),
    )
