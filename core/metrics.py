"""Deterministic metric formulas. Pure functions, no I/O, no LLM.

Each function computes one raw number from already-fetched inputs. These are
metric-definition-tier calculations (config/methodology.yaml) — they define
HOW a number is computed, not whether it indicates a good investment.
"""

from __future__ import annotations

from core.models import Evidence, MetricValue


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


def disrupted_flight_count(delayed_flights: int, cancelled_flights: int) -> int:
    """Sum of delayed + cancelled. Caller must ensure the two counts don't
    double-count the same flight per the source dataset's semantics."""
    return delayed_flights + cancelled_flights


def departures_per_runway(annual_departures: float, active_runway_count: int) -> float | None:
    """Descriptive context ONLY. Does not measure practical runway capacity
    — runway configuration, crossing geometry, aircraft mix, weather, and
    airspace all matter and are not captured here."""
    if active_runway_count <= 0:
        return None
    return annual_departures / active_runway_count


def as_metric_value(
    value: float | int | None,
    evidence: Evidence,
    unit: str | None = None,
    definition: str | None = None,
    note: str | None = None,
) -> MetricValue:
    """Convenience wrapper so tools/ don't construct MetricValue by hand and
    risk forgetting the evidence tag."""
    return MetricValue(
        value=value,
        evidence=evidence if value is not None else Evidence.MISSING,
        unit=unit,
        definition=definition,
        note=note,
    )
