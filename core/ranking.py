"""Deterministic ordered-tuple ranking. No synthetic 0-100 score — ranking
is a sort key built from classification levels and raw metrics, per
investment focus, per the approved plan Section 2.
"""

from __future__ import annotations

from core.models import Evidence, InvestmentFocus, Level, OpportunityAssessment

_LEVEL_RANK = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1, Level.INSUFFICIENT: 0}
_EVIDENCE_RANK = {Evidence.DIRECT: 1, Evidence.PROXY: 0, Evidence.MISSING: -1}


def _terminal_expansion_key(
    a: OpportunityAssessment,
    passenger_cagr: float,
    passenger_volume: float,
) -> tuple:
    return (
        _LEVEL_RANK[a.need_level.level],
        _LEVEL_RANK[a.passenger_side_pressure.level],
        _LEVEL_RANK[a.demand_level.level],
        _EVIDENCE_RANK[a.passenger_side_pressure.evidence],
        passenger_cagr,
        passenger_volume,
    )


def _runway_airfield_key(
    a: OpportunityAssessment,
    departure_delay_rate: float,
    departure_cagr: float,
    departure_volume: float,
) -> tuple:
    return (
        _LEVEL_RANK[a.need_level.level],
        _LEVEL_RANK[a.flight_side_pressure.level],
        _LEVEL_RANK[a.demand_level.level],
        _EVIDENCE_RANK[a.flight_side_pressure.evidence],
        departure_delay_rate,
        departure_cagr,
        departure_volume,
    )


def _general_modernization_key(
    a: OpportunityAssessment,
    growth: float,
    volume: float,
) -> tuple:
    high_side_count = sum(
        1 for level in (a.passenger_side_pressure.level, a.flight_side_pressure.level)
        if level == Level.HIGH
    )
    highest_pressure = max(
        _LEVEL_RANK[a.passenger_side_pressure.level],
        _LEVEL_RANK[a.flight_side_pressure.level],
    )
    best_evidence = max(
        _EVIDENCE_RANK[a.passenger_side_pressure.evidence],
        _EVIDENCE_RANK[a.flight_side_pressure.evidence],
    )
    return (
        _LEVEL_RANK[a.need_level.level],
        high_side_count,
        highest_pressure,
        _LEVEL_RANK[a.demand_level.level],
        best_evidence,
        growth,
        volume,
    )


def rank_candidates(
    assessments: list[OpportunityAssessment],
    investment_focus: InvestmentFocus,
    metrics_by_airport: dict[str, dict],
) -> list[OpportunityAssessment]:
    """metrics_by_airport maps airport code -> raw tie-break metric values
    (passenger_cagr, passenger_volume, departure_delay_rate, departure_cagr,
    departure_volume, growth, volume) needed for the final tie-break tiers.
    Ranking is restricted to exactly the assessments passed in — callers
    are responsible for building the requested candidate set upstream.
    """
    def sort_key(a: OpportunityAssessment) -> tuple:
        m = metrics_by_airport.get(a.airport.code, {})
        if investment_focus == InvestmentFocus.TERMINAL:
            return _terminal_expansion_key(
                a, m.get("passenger_cagr", 0.0), m.get("passenger_volume", 0.0)
            )
        if investment_focus == InvestmentFocus.RUNWAY_AIRFIELD:
            return _runway_airfield_key(
                a, m.get("departure_delay_rate", 0.0),
                m.get("departure_cagr", 0.0), m.get("departure_volume", 0.0),
            )
        return _general_modernization_key(
            a, m.get("growth", 0.0), m.get("volume", 0.0)
        )

    return sorted(assessments, key=sort_key, reverse=True)


def scope_label(candidate_set_description: str, is_national: bool) -> str:
    """Always phrase results as 'best among the airports evaluated,' never
    'best in the US,' unless a national candidate set was actually
    evaluated."""
    if is_national:
        return f"best among all evaluated US commercial airports"
    return f"best among the airports evaluated ({candidate_set_description})"
