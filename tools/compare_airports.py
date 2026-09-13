# ORIENTATION: TOOL. "Compare congestion at X and Y" -- side by side, flags period mismatches.
"""compare_airports: side-by-side congestion comparison for a small set of
airports over a common period, using core/scoring.py's congestion_score().
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from langchain_core.tools import tool

from core.metrics import latest_complete_year
from core.scoring import congestion_score
from tools.get_airport_profile import get_airport_profile


def compare_airports(airport_codes: list[str]) -> dict:
    if len(airport_codes) < 2:
        return {"error": "AMBIGUOUS_QUERY", "detail": "Provide at least two airport codes to compare."}

    year = latest_complete_year()
    results, errors = [], []

    with ThreadPoolExecutor(max_workers=max(len(airport_codes), 1)) as pool:
        profile_results = list(pool.map(get_airport_profile, airport_codes))

    for code, profile_result in zip(airport_codes, profile_results):
        if "error" in profile_result:
            errors.append({"airport_code": code, **profile_result})
            continue

        profile = profile_result["profile"]
        congestion = congestion_score(profile)

        results.append({
            "airport_code": code.strip().upper(),
            "airport_name": profile.airport.name,
            "hub_class": profile.airport.hub_class.value,
            "period": profile.period,
            "departure_delay_rate": profile.departure_delay_rate.model_dump(),
            "median_taxi_out_minutes": profile.median_taxi_out_minutes.model_dump(),
            "cancellation_rate": profile.cancellation_rate.model_dump(),
            "passenger_volume": profile.passengers.model_dump(),
            "congestion_score": congestion.value,
            "congestion_score_basis": congestion.basis,
            "congestion_score_missing": congestion.missing,
            "limitations": profile_result["limitations"] + congestion.limitations,
        })

    if len(results) < 2:
        return {"error": "INSUFFICIENT_DATA", "detail": "Fewer than two airports had usable data.", "partial_results": results, "errors": errors}

    periods = {r["airport_code"]: (r["departure_delay_rate"].get("source") or {}).get("coverage_period") for r in results}
    distinct_periods = {p for p in periods.values() if p is not None}
    period_mismatch = len(distinct_periods) > 1

    return {
        "comparison_period": str(year),
        "period_mismatch": period_mismatch,
        "periods_by_airport": periods,
        "note": (
            "congestion_score (0-100, higher = more congested) is computed from delay "
            "rate, taxi-out time, and cancellation-rate signals, applied consistently "
            "across the compared airports. Higher traffic volume alone is not equated "
            "with greater congestion -- see passenger_volume separately from congestion_score."
            + (" WARNING: the compared airports do not share the same actual data "
               "coverage period -- see periods_by_airport." if period_mismatch else "")
        ),
        "airports": results,
        "errors": errors,
    }


@tool
def compare_airports_tool(airport_codes: list[str]) -> dict:
    """Compare congestion between 2 or more airports over the same period,
    using a consistent congestion_score (0-100, higher = more congested)
    computed from delay rate, taxi-out time, and cancellation rate. Use for
    direct airport-vs-airport comparison questions (e.g. "compare LAX and
    SNA congestion")."""
    return compare_airports(airport_codes)
