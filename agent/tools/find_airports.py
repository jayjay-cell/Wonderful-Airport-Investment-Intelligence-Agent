"""find_airports tool: resolves a region/state/city/name query into a list
of candidate airport identities. Thin wiring over data/regions.py and
data/clients/faa_airports.py — no scoring logic.
"""

from __future__ import annotations

import httpx

from core.models import Airport, HubClass
from data.clients import faa_enplanements
from data.regions import resolve_region

_AIRPORTS_URL = (
    "https://services.arcgis.com/xOi1kZaI0eWDREZv/arcgis/rest/services/"
    "NTAD_Aviation_Facilities/FeatureServer/0/query"
)
_TIMEOUT = 20.0

_HUB_CODE_MAP = {
    "L": HubClass.LARGE,
    "M": HubClass.MEDIUM,
    "S": HubClass.SMALL,
    "N": HubClass.NONHUB,
}

# FAA's regulatory definition of "commercial service airport": publicly
# owned, public-use, with at least 2,500 passenger boardings (enplanements)
# per calendar year (49 U.S.C. § 47102(7)). Discovered during implementation
# that the raw FAA enplanements file lists EVERY airport with any recorded
# enplanement count, including small general-aviation fields with single-
# digit annual boardings (e.g. a handful of charter/diverted flights) — a
# simple "has an enplanements record" check is not sufficient to identify
# commercial-service airports and silently includes GA fields.
_COMMERCIAL_SERVICE_MIN_ENPLANEMENTS = 2500


def find_airports(
    region: str | None = None,
    state: str | None = None,
    commercial_only: bool = True,
) -> dict:
    """Resolves a region name (e.g. "New England") or a single state code/
    name into a list of commercial airports, using the FAA's live ArcGIS
    endpoint filtered by state, then cross-referenced against FAA
    enplanements (for commercial-service confirmation and hub class).

    Exactly one of `region` or `state` should normally be provided; if both
    are given, `region` takes precedence.
    """
    if region:
        states = resolve_region(region)
        if states is None:
            return {
                "error": "AMBIGUOUS_QUERY",
                "detail": f"{region!r} is not a recognized region. Known "
                          f"regions: New England, Mid-Atlantic, Midwest, "
                          f"South, Southwest, West, Pacific. Try passing a "
                          f"specific state instead.",
            }
    elif state:
        states = [state.strip().upper()]
    else:
        return {
            "error": "AMBIGUOUS_QUERY",
            "detail": "Provide either a region name or a state code/name.",
        }

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
        return {
            "error": "SOURCE_UNAVAILABLE",
            "detail": f"FAA Airports query failed: {exc}",
        }

    airports: list[Airport] = []
    enplanements_by_code: dict[str, int] = {}
    for feature in data.get("features", []):
        attrs = feature["attributes"]
        code = attrs.get("ARPT_ID")
        if not code:
            continue

        enp = faa_enplanements.get_enplanements(code)
        is_commercial = (
            enp is not None
            and (enp.get("enplanements_current_year") or 0) >= _COMMERCIAL_SERVICE_MIN_ENPLANEMENTS
        )

        if commercial_only and not is_commercial:
            continue

        airports.append(Airport(
            code=code,
            name=attrs.get("ARPT_NAME", ""),
            city=attrs.get("CITY", ""),
            state=attrs.get("STATE_CODE", ""),
            region=region,
            hub_class=enp["hub_class"] if enp else HubClass.UNKNOWN,
            is_commercial=is_commercial,
        ))
        if enp is not None:
            enplanements_by_code[code] = enp.get("enplanements_current_year") or 0

    return {
        "airports": airports,
        "candidate_set_description": region or ", ".join(states),
        "count": len(airports),
        # Enplanement counts already fetched above (free — no extra network
        # call). Used by rank_airports as a cheap pre-screen so the
        # expensive per-airport pipeline (BTS T-100 scrape + On-Time
        # download) only runs on a shortlist, not every commercial airport
        # in a large region.
        "enplanements_by_code": enplanements_by_code,
    }
