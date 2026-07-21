"""Great-circle distance (Haversine)."""

from __future__ import annotations

import math

# Mean Earth radius (IUGG), in metres. Good to well within the accuracy
# needed for a fixed-installation plausibility check.
EARTH_RADIUS_M = 6_371_008.8


def haversine_m(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """Return the great-circle distance between two points, in metres.

    Inputs are decimal degrees (WGS-84). The Haversine formula is used;
    it is numerically stable for the small distances relevant here.
    """
    phi1 = math.radians(lat1_deg)
    phi2 = math.radians(lat2_deg)
    d_phi = math.radians(lat2_deg - lat1_deg)
    d_lambda = math.radians(lon2_deg - lon1_deg)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c