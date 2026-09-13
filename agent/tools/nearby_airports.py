"""find_nearby_airports tool: which airports are closest to a given one,
with real computed distances.

Fixes a real gap found in live use: asked "which airport is nearest to
JFK", the agent either invented mileage from memory (wrong) or declined
entirely (also wrong — the data exists). FAA publishes LAT_DECIMAL /
LONG_DECIMAL for every airport, and distance is deterministic arithmetic
on those coordinates, so this is a calculable answer, not a remembered
fact.

Implementation: one bounding-box query to the FAA ArcGIS endpoint around
the origin airport (server-side filtered — does NOT download all ~20k US
airports), then exact great-circle distances computed in core/metrics.py
and sorted deterministically here.
"""

from __future__ import annotations

import httpx

from core.metrics import great_circle_distance_miles
from core.timing import timed
from data.cache import get_or_load
from data.clients import faa_airports, faa_enplanements
from data.degradation import SourceUnavailable

_AIRPORTS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Aviation_Facilities/FeatureServer/0/query"
)
_TIMEOUT = 20.0

# FAA's regulatory threshold for a "commercial service" airport — same
# definition used in find_airports.py, so "nearest airport" doesn't return
# grass strips and heliports when the user means a real airport.
_COMMERCIAL_SERVICE_MIN_ENPLANEMENTS = 2500

# Degrees of latitude/longitude to search around the origin. ~1 degree of
# latitude is ~69 miles, so 2.5 degrees covers roughly a 170-mile radius —
# generous enough to always find neighbors, small enough that the query
# stays fast and server-side-filtered.
_SEARCH_DEGREES = 2.5


def find_nearby_airports(
    airport_code: str,
    limit: int = 5,
    commercial_only: bool = True,
) -> dict:
    """Returns the airports nearest to `airport_code`, with real
    great-circle distances in statute miles."""
    code = airport_code.strip().upper()
    limit = max(1, min(limit, 20))

    try:
        origin, origin_source = faa_airports.get_airport_identity(code)
    except SourceUnavailable as exc:
        return {
            "error": "INVALID_AIRPORT",
            "detail": f"Could not resolve airport {code!r}: {exc.detail}",
        }

    if origin.latitude is None or origin.longitude is None:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": f"FAA record for {code} has no published coordinates, "
                      f"so distances cannot be calculated.",
        }

    lat, lon = origin.latitude, origin.longitude
    cache_key = f"faa_airports:bbox:{round(lat, 2)}:{round(lon, 2)}:{_SEARCH_DEGREES}"

    def _load_bbox():
        params = {
            "where": (
                f"LAT_DECIMAL BETWEEN {lat - _SEARCH_DEGREES} AND {lat + _SEARCH_DEGREES} "
                f"AND LONG_DECIMAL BETWEEN {lon - _SEARCH_DEGREES} AND {lon + _SEARCH_DEGREES}"
            ),
            "outFields": "ARPT_ID,ARPT_NAME,CITY,STATE_CODE,LAT_DECIMAL,LONG_DECIMAL",
            "f": "json",
            "resultRecordCount": "2000",
        }
        resp = httpx.get(_AIRPORTS_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    try:
        with timed("nearby_airports.bbox_query", airport=code):
            data = get_or_load(cache_key, _load_bbox, ttl_seconds=24 * 60 * 60)
    except Exception as exc:
        return {
            "error": "SOURCE_UNAVAILABLE",
            "detail": f"FAA Airports and Runways query failed: {exc}",
        }

    candidates = []
    for feature in data.get("features", []):
        attrs = feature["attributes"]
        other_code = attrs.get("ARPT_ID")
        other_lat = attrs.get("LAT_DECIMAL")
        other_lon = attrs.get("LONG_DECIMAL")
        if not other_code or other_code == code:
            continue
        if other_lat is None or other_lon is None:
            continue

        enp = faa_enplanements.get_enplanements(other_code)
        enplanements = (enp.get("enplanements_current_year") or 0) if enp else 0

        if commercial_only and enplanements < _COMMERCIAL_SERVICE_MIN_ENPLANEMENTS:
            continue

        hub_class = enp["hub_class"].value if (enp and hasattr(enp.get("hub_class"), "value")) else "unknown"

        distance = great_circle_distance_miles(lat, lon, other_lat, other_lon)
        candidates.append({
            "airport_code": other_code,
            "airport_name": attrs.get("ARPT_NAME"),
            "city": attrs.get("CITY"),
            "state": attrs.get("STATE_CODE"),
            "distance_miles": round(distance, 1),
            # Scale context so the caller (and ultimately the user) can tell
            # a major hub from a technically-commercial-service facility.
            # FAA's commercial-service bar is only 2,500 enplanements/year,
            # so seaplane bases and very small fields legitimately qualify —
            # we surface that rather than silently filtering, and let the
            # user decide what counts as "an airport" for their purpose.
            "annual_enplanements": enplanements,
            "hub_class": hub_class,
        })

    candidates.sort(key=lambda c: c["distance_miles"])

    if not candidates:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": f"No {'commercial-service ' if commercial_only else ''}airports "
                      f"found within roughly {int(_SEARCH_DEGREES * 69)} miles of {code}.",
        }

    return {
        "origin_airport": code,
        "origin_name": origin.name,
        "nearby_airports": candidates[:limit],
        "commercial_only": commercial_only,
        "distance_definition": (
            "Great-circle (straight-line) distance between published FAA "
            "airport reference points, in statute miles. Not driving distance."
        ),
        "scale_note": (
            "Results include every FAA commercial-service airport in range. "
            "FAA's commercial-service threshold is only 2,500 annual "
            "enplanements, so small facilities (seaplane bases, regional "
            "fields) can appear alongside major hubs — compare the "
            "annual_enplanements and hub_class of each result rather than "
            "assuming every entry is a comparable airport."
        ),
        "source_name": "FAA Airports and Runways (ArcGIS NTAD_Aviation_Facilities)",
        "coverage_period": origin_source.coverage_period,
    }
