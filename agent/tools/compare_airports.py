"""compare_airports tool: side-by-side congestion/pressure comparison for a
small set of airports over a common period. This is a lookup/comparison
query per the plan's query-type split — it reuses Congestion Level's signal
set but does not run the full opportunity-assessment pipeline (no
bottleneck inference, no gates, no research).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import yaml

from agent.tools.airport_profile import get_airport_profile
from core.classifications import classify_congestion
from core.service_period import latest_complete_year

with open("config/methodology.yaml") as _f:
    _CONFIG = yaml.safe_load(_f)


def compare_airports(airport_codes: list[str]) -> dict:
    if len(airport_codes) < 2:
        return {
            "error": "AMBIGUOUS_QUERY",
            "detail": "Provide at least two airport codes to compare.",
        }

    year = latest_complete_year()
    results = []
    errors = []

    # Fetch all airport profiles concurrently — each is already internally
    # parallelized across its 5 data sources (see get_airport_profile), so
    # this bounds an N-airport comparison to roughly one profile's worth of
    # wall-clock time instead of N times that.
    with ThreadPoolExecutor(max_workers=max(len(airport_codes), 1)) as pool:
        profile_results = list(pool.map(get_airport_profile, airport_codes))

    for code, profile_result in zip(airport_codes, profile_results):
        if "error" in profile_result:
            errors.append({"airport_code": code, **profile_result})
            continue

        profile = profile_result["profile"]
        congestion = classify_congestion(
            _CONFIG,
            departure_delay_rate=profile.departure_delay_rate.value,
            median_taxi_out_minutes=profile.median_taxi_out_minutes.value,
            cancellation_rate=profile.cancellation_rate.value,
        )

        results.append({
            "airport_code": code.strip().upper(),
            "airport_name": profile.airport.name,
            "hub_class": profile.airport.hub_class.value,
            "period": profile.period,
            "departure_delay_rate": profile.departure_delay_rate.model_dump(),
            "median_taxi_out_minutes": profile.median_taxi_out_minutes.model_dump(),
            "cancellation_rate": profile.cancellation_rate.model_dump(),
            "passenger_volume": profile.passengers.model_dump(),
            "congestion_level": congestion.level.value,
            "congestion_signals_triggered": congestion.signals_triggered,
            "limitations": profile_result["limitations"],
        })

    if len(results) < 2:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": "Fewer than two airports had usable data.",
            "partial_results": results,
            "errors": errors,
        }

    return {
        "comparison_period": str(year),
        "note": (
            "Congestion Level is a proxy classification derived from delay "
            "rate, taxi-out time, and cancellation-rate signals, applied "
            "consistently across the compared airports. Higher traffic "
            "volume alone is not equated with greater congestion — see "
            "passenger_volume separately from congestion_level."
        ),
        "airports": results,
        "errors": errors,
    }
