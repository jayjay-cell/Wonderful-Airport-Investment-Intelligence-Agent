"""calculate_long_haul_share tool: a pure lookup/calculation, not a full
opportunity assessment. Per the plan's query-type split, this does not
trigger classification, gates, or research — just data + metric-definition
calculation.
"""

from __future__ import annotations

from core.metrics import long_haul_share
from core.service_period import latest_complete_year
from data.clients import bts_t100
from data.degradation import SourceUnavailable

_DEFAULT_THRESHOLD_MILES = 1500
_DEFAULT_BASIS = "departures"


def calculate_long_haul_share(
    airport_code: str,
    threshold_miles: float = _DEFAULT_THRESHOLD_MILES,
    basis: str = _DEFAULT_BASIS,
    year: int | None = None,
) -> dict:
    code = airport_code.strip().upper()
    year = year or latest_complete_year()

    try:
        result = bts_t100.get_annual_routes(code, year)
    except SourceUnavailable as exc:
        return {
            "error": "SOURCE_UNAVAILABLE",
            "detail": f"Could not retrieve T-100 route data for {code}: {exc.detail}",
        }

    if result["route_count"] == 0:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": result.get("note", f"No route data found for {code} in {year}."),
        }

    adapted = bts_t100.adapt_for_long_haul_share(result["routes"])
    if not adapted:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": f"{code} has T-100 records for {year}, but none with "
                      f"passenger service (all-cargo airport, or no scheduled "
                      f"passenger service this year).",
        }

    share = long_haul_share(adapted, threshold_miles=threshold_miles, basis=basis)

    return {
        "airport_code": code,
        "year": year,
        "numerator": share["numerator"],
        "denominator": share["denominator"],
        "percentage": share["percentage"],
        "threshold_miles": share["threshold_miles"],
        "basis": share["basis"],
        "definition": (
            f"Long-haul share = routes with great-circle distance >= "
            f"{threshold_miles} statute miles, as a share of scheduled "
            f"passenger {basis} (cargo-only operations excluded)."
        ),
        "source_name": result["source_name"],
        "coverage_period": str(year),
        "cache_status": result["cache_status"],
    }
