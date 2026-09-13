# ORIENTATION: TOOL. Resolves a region/state name into a list of real commercial airports.
"""find_airports: resolves a region/state/city/name query into a list of
candidate airport identities. Thin wiring over data/regions.py and
data/clients/faa_airports.py -- no scoring logic.
"""

from __future__ import annotations

import httpx
from langchain_core.tools import tool

from core.models import Airport, HubClass
from data.clients import faa_enplanements
from data.regions import resolve_region

_AIRPORTS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Aviation_Facilities/FeatureServer/0/query"
)
_TIMEOUT = 20.0

# FAA's regulatory definition of "commercial service airport": publicly
# owned, public-use, with at least 2,500 passenger boardings per calendar
# year (49 U.S.C. Sec. 47102(7)). The raw FAA enplanements file lists every
# airport with any recorded enplanement count, including small GA fields
# with single-digit annual boardings -- this threshold filters those out.
_COMMERCIAL_SERVICE_MIN_ENPLANEMENTS = 2500

_NATIONAL_SCOPE_PHRASES = {
    "united states", "united states of america", "usa", "us", "u.s.", "u.s.a.",
    "america", "national", "nationally", "nationwide", "all", "all us airports",
    "entire united states", "whole country", "country",
}


def is_national_scope(region: str) -> bool:
    """True when 'region' actually means 'no geographic filter' -- e.g.
    "which airport is largest in the United States" naturally produces
    region='United States', which is not a named region and must not be
    reported as an unrecognized one."""
    return region.strip().lower().strip(".") in _NATIONAL_SCOPE_PHRASES


def find_airports(region: str | None = None, state: str | None = None, commercial_only: bool = True) -> dict:
    """Resolves a region name (e.g. "New England") or a single state code/
    name into a list of commercial airports, using FAA's live ArcGIS
    endpoint filtered by state, cross-referenced against FAA enplanements
    for commercial-service confirmation and hub class."""
    if region:
        if is_national_scope(region):
            return {
                "error": "AMBIGUOUS_QUERY",
                "detail": "A nationwide airport list is out of scope for this tool -- "
                          "it resolves a bounded region or state. Name a specific region "
                          "(e.g. New England) or state, or ask for a national ranking instead.",
            }
        states = resolve_region(region)
        if states is None:
            return {
                "error": "AMBIGUOUS_QUERY",
                "detail": f"{region!r} is not a recognized region. Known regions: "
                          f"New England, Mid-Atlantic, Midwest, South, Southwest, West, "
                          f"Pacific. Try passing a specific state instead.",
            }
    elif state:
        states = [state.strip().upper()]
    else:
        return {"error": "AMBIGUOUS_QUERY", "detail": "Provide either a region name or a state code/name."}

    where_clause = " OR ".join(f"STATE_CODE='{s}'" for s in states)
    params = {
        "where": where_clause,
        "outFields": "ARPT_ID,ARPT_NAME,CITY,STATE_CODE,FACILITY_USE_CODE",
        "f": "json",
        "resultRecordCount": "2000",
    }
    try:
        resp = httpx.get(_AIRPORTS_URL, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"error": "SOURCE_UNAVAILABLE", "detail": f"FAA Airports query failed: {exc}"}

    airports: list[Airport] = []
    enplanements_by_code: dict[str, int] = {}
    for feature in data.get("features", []):
        attrs = feature["attributes"]
        code = attrs.get("ARPT_ID")
        if not code:
            continue

        enp = faa_enplanements.get_enplanements(code)
        is_commercial = enp is not None and (enp.get("enplanements_current_year") or 0) >= _COMMERCIAL_SERVICE_MIN_ENPLANEMENTS

        if commercial_only and not is_commercial:
            continue

        airports.append(Airport(
            code=code, name=attrs.get("ARPT_NAME", ""), city=attrs.get("CITY", ""),
            state=attrs.get("STATE_CODE", ""), region=region,
            hub_class=enp["hub_class"] if enp else HubClass.UNKNOWN,
            is_commercial=is_commercial,
        ))
        if enp is not None:
            enplanements_by_code[code] = enp.get("enplanements_current_year") or 0

    return {
        "airports": airports,
        "candidate_set_description": region or ", ".join(states),
        "count": len(airports),
        "enplanements_by_code": enplanements_by_code,
    }


def _serialize(result: dict) -> dict:
    if "error" in result:
        return result
    return {**result, "airports": [a.model_dump() for a in result["airports"]]}


@tool
def find_airports_tool(region: str = "", state: str = "", commercial_only: bool = True) -> dict:
    """Find commercial-service US airports by region (e.g. "New England") or
    by a single state name/code. Provide either region or state, not both.
    Returns airport identities, location, and hub class. Use this first
    when a question refers to a region or state rather than a specific
    airport code."""
    return _serialize(find_airports(region=region or None, state=state or None, commercial_only=commercial_only))
