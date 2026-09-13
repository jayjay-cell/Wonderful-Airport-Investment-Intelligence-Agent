"""FAA Airports and Runways connector.

Live, no-auth REST/JSON API (ArcGIS FeatureServer), verified working during
the feasibility spike. Queries only the requested airport(s) — never bulk
downloads the national dataset.
"""

from __future__ import annotations

import httpx

from core.models import Airport, HubClass, SourceRecord
from data.cache import cache_get, cache_set
from data.degradation import SourceUnavailable, with_retry
from datetime import date

_AIRPORTS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Aviation_Facilities/FeatureServer/0/query"
)
_RUNWAYS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "Runways_View/FeatureServer/0/query"
)

_TIMEOUT = 10.0


def get_airport_identity(airport_code: str) -> tuple[Airport, SourceRecord]:
    """Fetch identity/location/ownership for one airport code. Raises
    SourceUnavailable if the live query fails after retry."""
    code = airport_code.strip().upper()
    cache_key = f"faa_airports:identity:{code}"
    cached = cache_get(cache_key)
    if cached is not None:
        airport, source = cached
        source = source.model_copy(update={"origin": "cache"})
        return airport, source

    def _fetch():
        params = {
            "where": f"ARPT_ID='{code}'",
            "outFields": "ARPT_ID,ARPT_NAME,CITY,STATE_CODE,OWNERSHIP_TYPE_CODE,FACILITY_USE_CODE",
            "f": "json",
        }
        resp = httpx.get(_AIRPORTS_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    try:
        data = with_retry(_fetch, retries=1)
    except Exception as exc:
        raise SourceUnavailable(
            source_name="FAA Airports and Runways",
            detail=f"Live ArcGIS query failed for {code}: {exc}",
        ) from exc

    features = data.get("features", [])
    if not features:
        raise SourceUnavailable(
            source_name="FAA Airports and Runways",
            detail=f"No airport record found for code {code!r}. This may "
                   f"indicate an invalid or non-FAA-tracked airport code.",
        )

    attrs = features[0]["attributes"]
    runway_count = _get_runway_count(code)

    airport = Airport(
        code=attrs["ARPT_ID"],
        name=attrs["ARPT_NAME"],
        city=attrs["CITY"],
        state=attrs["STATE_CODE"],
        is_commercial=attrs.get("FACILITY_USE_CODE") == "PU",
        runway_count=runway_count,
    )
    source = SourceRecord(
        source_name="FAA Airports and Runways (ArcGIS NTAD_Aviation_Facilities)",
        retrieved_at=date.today(),
        coverage_period="current (28-day AIRAC cycle)",
        origin="live",
    )
    cache_set(cache_key, (airport, source))
    return airport, source


def _get_runway_count(code: str) -> int | None:
    try:
        params = {
            "where": f"ARPT_ID='{code}'",
            "outFields": "RWY_ID",
            "f": "json",
        }
        resp = httpx.get(_RUNWAYS_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return len(data.get("features", []))
    except Exception:
        return None
