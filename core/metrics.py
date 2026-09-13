"""Deterministic metric formulas. Pure functions, no I/O, no LLM.

Each function computes one raw number from already-fetched inputs. These are
metric-definition-tier calculations (config/methodology.yaml) — they define
HOW a number is computed, not whether it indicates a good investment.
"""

from __future__ import annotations

from datetime import date

from core.models import Evidence, MetricValue


def latest_complete_year(today: date | None = None) -> int:
    """FAA/BTS annual data for year Y is generally not complete/published
    until well into Y+1. Conservatively treat last calendar year as the
    latest complete year once we're at least 3 months into the current one,
    otherwise the year before that."""
    today = today or date.today()
    if today.month >= 4:
        return today.year - 1
    return today.year - 2


def trailing_years(n: int, today: date | None = None) -> list[int]:
    end = latest_complete_year(today)
    return list(range(end - n + 1, end + 1))


def passenger_volume(passenger_records: list[float]) -> float:
    """Sum of relevant passenger records. Caller is responsible for having
    already selected enplaned/deplaned/total consistently — this function
    does not disambiguate that choice."""
    return sum(passenger_records)


def load_factor(total_passengers: float, total_seats: float) -> float | None:
    """Load Factor = sum(passengers) / sum(seats), computed on totals.

    Never average row-level load factors — always sum both sides first.
    """
    if total_seats <= 0:
        return None
    return total_passengers / total_seats


def cagr(starting_value: float, ending_value: float, years: float) -> float | None:
    """Compound Annual Growth Rate over `years` complete comparable periods."""
    if starting_value <= 0 or years <= 0:
        return None
    return (ending_value / starting_value) ** (1 / years) - 1


def passengers_per_departure(passengers: float, departures_performed: float) -> float | None:
    """Passenger-density proxy, NOT a measurement of terminal utilization."""
    if departures_performed <= 0:
        return None
    return passengers / departures_performed


def delay_rate(delayed_flights: int, eligible_flights: int) -> float | None:
    """Fraction of eligible flights that were delayed per the underlying
    dataset's delay threshold (caller must state that threshold alongside
    this value)."""
    if eligible_flights <= 0:
        return None
    return delayed_flights / eligible_flights


def cancellation_rate(cancelled_flights: int, scheduled_flights: int) -> float | None:
    if scheduled_flights <= 0:
        return None
    return cancelled_flights / scheduled_flights


def long_haul_share(
    routes: list[dict],
    threshold_miles: float,
    basis: str = "departures",
) -> dict:
    """Long-haul share of departures or passengers.

    routes: list of {"distance_miles": float, "departures": float,
                      "passengers": float}
    basis: "departures" or "passengers"

    Returns numerator, denominator, percentage, and the definition used —
    all of which must be surfaced in any answer, per methodology.yaml's
    metric_definition tier.
    """
    if basis not in ("departures", "passengers"):
        raise ValueError(f"basis must be 'departures' or 'passengers', got {basis!r}")

    key = basis
    numerator = sum(r[key] for r in routes if r["distance_miles"] >= threshold_miles)
    denominator = sum(r[key] for r in routes)

    percentage = (numerator / denominator) if denominator > 0 else None

    return {
        "numerator": numerator,
        "denominator": denominator,
        "percentage": percentage,
        "threshold_miles": threshold_miles,
        "basis": basis,
    }


_EARTH_RADIUS_STATUTE_MILES = 3958.7613


def great_circle_distance_miles(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle (haversine) distance in statute miles between two
    coordinates. Deterministic arithmetic on FAA-published airport
    coordinates — not a remembered fact. Used for "how far apart are these
    airports" / "what's nearest to X" questions so the agent computes the
    answer rather than recalling (and potentially inventing) a number.

    Note: this is straight-line distance between airport reference points,
    not driving distance.
    """
    import math

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_STATUTE_MILES * math.asin(math.sqrt(a))


def as_metric_value(
    value: float | int | None,
    evidence: Evidence,
    unit: str | None = None,
    definition: str | None = None,
    note: str | None = None,
    source=None,
) -> MetricValue:
    """Convenience wrapper so tools/ don't construct MetricValue by hand and
    risk forgetting the evidence tag. `source` (a core.models.SourceRecord)
    should be passed whenever the caller knows the metric's ACTUAL coverage
    period — e.g. FAA enplanements (annual, published with a lag) vs. BTS
    On-Time (may already cover a later year/month) must never be conflated
    into one shared period just because both live on the same profile."""
    return MetricValue(
        value=value,
        evidence=evidence if value is not None else Evidence.MISSING,
        unit=unit,
        definition=definition,
        note=note,
        source=source,
    )
