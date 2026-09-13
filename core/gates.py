"""Hard gates, soft flags, and Actionability derivation.

A hard gate prevents "Strong candidate" but never erases Need — a High-Need
airport with a triggered hard gate correctly resolves to
"High need, low actionability", not to being silently dropped.
"""

from __future__ import annotations

from core.models import (
    Actionability,
    Bottleneck,
    HardGate,
    InvestmentFit,
    Level,
    ResearchEvidence,
    SoftFlag,
)

_NEVER_ADDRESSABLE_BY_PHYSICAL_INVESTMENT = {Bottleneck.AIRSPACE_ATC, Bottleneck.WEATHER}


def evaluate_hard_gates(
    investment_fit: InvestmentFit,
    likely_bottleneck: Bottleneck,
    research_evidence: list[ResearchEvidence],
    critical_data_conflict: bool,
) -> list[HardGate]:
    gates: list[HardGate] = []

    gates.append(HardGate(
        reason="requested_investment_does_not_address_confirmed_bottleneck",
        triggered=investment_fit == InvestmentFit.MISMATCH,
        detail="The requested investment type does not address the "
               "identified bottleneck." if investment_fit == InvestmentFit.MISMATCH else None,
    ))

    weather_or_airspace_dominant = likely_bottleneck in _NEVER_ADDRESSABLE_BY_PHYSICAL_INVESTMENT
    gates.append(HardGate(
        reason="weather_or_airspace_dominant_for_physical_project",
        triggered=weather_or_airspace_dominant,
        detail="The dominant constraint is weather or external airspace/ATC, "
               "which a physical airport investment cannot resolve." if weather_or_airspace_dominant else None,
    ))

    legal_cap = any(
        ev.field == "legal_capacity_cap" and ev.status == "supported" and ev.value
        for ev in research_evidence
    )
    gates.append(HardGate(
        reason="binding_legal_capacity_cap",
        triggered=legal_cap,
        detail="A binding legal capacity cap was confirmed by research evidence." if legal_cap else None,
    ))

    physically_impossible = any(
        ev.field == "physical_impossibility" and ev.status == "supported" and ev.value
        for ev in research_evidence
    )
    gates.append(HardGate(
        reason="confirmed_physical_impossibility",
        triggered=physically_impossible,
        detail="Research evidence confirms the proposed expansion is "
               "physically infeasible at this site." if physically_impossible else None,
    ))

    funded_and_building = any(
        ev.field == "funding_status" and ev.status == "supported" and ev.value == "fully_funded_under_construction"
        for ev in research_evidence
    )
    gates.append(HardGate(
        reason="equivalent_project_already_funded_and_under_construction",
        triggered=funded_and_building,
        detail="An equivalent project is already fully funded and under "
               "construction." if funded_and_building else None,
    ))

    gates.append(HardGate(
        reason="critical_core_data_unavailable_or_conflicted",
        triggered=critical_data_conflict,
        detail="Critical core data required for this classification is "
               "missing or unresolvably conflicted." if critical_data_conflict else None,
    ))

    return gates


def evaluate_soft_flags(
    demand_level: Level,
    passenger_cagr: float | None,
    research_evidence: list[ResearchEvidence],
    research_invoked: bool,
    unresolved_noncritical_conflict: bool,
) -> list[SoftFlag]:
    flags: list[SoftFlag] = []

    if demand_level == Level.LOW and passenger_cagr is not None and passenger_cagr < 0:
        flags.append(SoftFlag(
            reason="sustained_demand_decline",
            detail=f"Passenger CAGR is negative ({passenger_cagr:.1%}).",
        ))

    opposition = [ev for ev in research_evidence
                  if ev.field == "community_opposition" and ev.status == "supported"]
    if opposition:
        flags.append(SoftFlag(
            reason="environmental_or_community_opposition",
            detail=opposition[0].claim,
        ))

    failed_proposal = [ev for ev in research_evidence
                        if ev.field == "previous_failed_proposal" and ev.status == "supported"]
    if failed_proposal:
        flags.append(SoftFlag(
            reason="previous_failed_proposal",
            detail=failed_proposal[0].claim,
        ))

    stale_study = [ev for ev in research_evidence
                    if ev.field == "capacity_study_date" and ev.status == "supported"]
    if stale_study:
        flags.append(SoftFlag(
            reason="stale_capacity_study",
            detail=f"Most recent capacity study: {stale_study[0].claim}",
        ))

    if not research_invoked:
        flags.append(SoftFlag(
            reason="incomplete_research",
            detail="Research Agent was not invoked for this airport; "
                   "Actionability-relevant fields default to Unknown.",
        ))

    if unresolved_noncritical_conflict:
        flags.append(SoftFlag(
            reason="unresolved_noncritical_data_conflict",
            detail="A non-critical data conflict was recorded but did not "
                   "block classification.",
        ))

    return flags


def derive_actionability(
    investment_fit: InvestmentFit,
    hard_gates: list[HardGate],
    research_invoked: bool,
) -> Actionability:
    if any(g.triggered for g in hard_gates):
        return Actionability.BLOCKED

    if investment_fit == InvestmentFit.MISMATCH:
        return Actionability.BLOCKED

    if not research_invoked:
        return Actionability.UNKNOWN

    if investment_fit in (InvestmentFit.CONFIRMED, InvestmentFit.LIKELY):
        return Actionability.ACTIONABLE

    return Actionability.CONDITIONAL
