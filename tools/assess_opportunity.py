# ORIENTATION: TOOL. Full 7-dimension opportunity assessment for ONE airport. All classification comes from core/.
"""assess_airport_opportunity: the full seven-dimension opportunity
assessment for one airport. This is where core/'s classification,
bottleneck, gates, and confidence logic gets wired to real data + optional
research evidence.
"""

from __future__ import annotations

import yaml
from langchain_core.tools import tool

from core.bottleneck import derive_investment_fit, infer_bottleneck
from core.classifications import classify_demand, classify_flight_side_pressure, classify_need, classify_passenger_side_pressure
from core.confidence import derive_confidence, derive_final_classification
from core.gates import derive_actionability, evaluate_hard_gates, evaluate_soft_flags
from core.models import InvestmentFocus, ObjectiveType, OpportunityAssessment
from tools.get_airport_profile import get_airport_profile

with open("config/methodology.yaml") as _f:
    _CONFIG = yaml.safe_load(_f)

_FOCUS_TO_OBJECTIVE = {
    InvestmentFocus.TERMINAL: ObjectiveType.PASSENGER_CAPACITY,
    InvestmentFocus.GATES: ObjectiveType.PASSENGER_CAPACITY,
    InvestmentFocus.RUNWAY_AIRFIELD: ObjectiveType.FLIGHT_CAPACITY,
    InvestmentFocus.OPERATIONS_TECHNOLOGY: ObjectiveType.BOTH,
    InvestmentFocus.GENERAL_MODERNIZATION: ObjectiveType.BOTH,
}


def assess_airport_opportunity(airport_code: str, investment_focus: str = "general_modernization", research: bool = False, include_bts: bool = True) -> dict:
    """include_bts=False produces a fast, FAA-only screening assessment
    (Demand Level only). Used by rank_airports's pre-screen pass; never the
    default for a direct single-airport question."""
    try:
        focus = InvestmentFocus(investment_focus)
    except ValueError:
        return {"error": "AMBIGUOUS_QUERY", "detail": f"Unrecognized investment_focus {investment_focus!r}. Valid values: {[f.value for f in InvestmentFocus]}."}

    profile_result = get_airport_profile(airport_code, include_bts=include_bts)
    if "error" in profile_result:
        return profile_result

    profile = profile_result["profile"]
    limitations = list(profile_result["limitations"])

    demand = classify_demand(
        passenger_yoy_growth=profile.passenger_yoy_growth.value,
        faa_forecast_cagr=profile.faa_forecast_passenger_cagr.value,
        config=_CONFIG,
    )
    passenger_pressure = classify_passenger_side_pressure(
        _CONFIG,
        terminal_utilization=profile.terminal_utilization.value if profile.terminal_utilization else None,
        load_factor=profile.load_factor.value,
        passengers_per_departure_growth=None,
        passenger_departure_cagr_gap_pp=None,
    )
    flight_pressure = classify_flight_side_pressure(
        _CONFIG,
        runway_utilization=profile.runway_utilization.value if profile.runway_utilization else None,
        departure_delay_rate=profile.departure_delay_rate.value,
        median_taxi_out_minutes=profile.median_taxi_out_minutes.value,
        cancellation_rate=profile.cancellation_rate.value,
    )

    relevant_pressure = (
        passenger_pressure if focus in (InvestmentFocus.TERMINAL, InvestmentFocus.GATES)
        else flight_pressure if focus == InvestmentFocus.RUNWAY_AIRFIELD
        else max([passenger_pressure, flight_pressure], key=lambda c: c.level.value)
    )
    need = classify_need(demand.level, relevant_pressure.level, _CONFIG)

    research_evidence = []  # typed research evidence is not yet populated -- see research_facts.py

    likely_bottleneck, bottleneck_reasoning = infer_bottleneck(
        passenger_side_pressure=passenger_pressure.level,
        flight_side_pressure=flight_pressure.level,
        delay_cause_breakdown=profile.delay_cause_breakdown,
        research_evidence=research_evidence,
    )

    bottleneck_is_direct = any(ev.field == "confirmed_bottleneck" and ev.status.value == "supported" for ev in research_evidence)
    investment_fit = derive_investment_fit(focus, likely_bottleneck, bottleneck_is_direct)

    hard_gates = evaluate_hard_gates(
        investment_fit=investment_fit, likely_bottleneck=likely_bottleneck,
        research_evidence=research_evidence, critical_data_conflict=False,
    )
    soft_flags = evaluate_soft_flags(
        demand_level=demand.level, passenger_yoy_growth=profile.passenger_yoy_growth.value,
        research_evidence=research_evidence, research_invoked=research,
        unresolved_noncritical_conflict=False,
    )
    actionability = derive_actionability(investment_fit, hard_gates, research_invoked=research)

    confidence, confidence_reason = derive_confidence(demand, passenger_pressure, flight_pressure, critical_data_conflict=False)
    final_classification = derive_final_classification(
        need_level=need.level, investment_fit=investment_fit, actionability=actionability,
        hard_gates=hard_gates, confidence=confidence,
    )

    if not research:
        limitations.append("Research was not invoked for this assessment -- Actionability defaults to Unknown unless a hard gate or clear mismatch is already evident from structured data alone.")

    assessment = OpportunityAssessment(
        airport=profile.airport, investment_focus=focus, objective=_FOCUS_TO_OBJECTIVE[focus],
        demand_level=demand, passenger_side_pressure=passenger_pressure, flight_side_pressure=flight_pressure,
        need_level=need, likely_bottleneck=likely_bottleneck, bottleneck_reasoning=bottleneck_reasoning,
        investment_fit=investment_fit, actionability=actionability, hard_gates=hard_gates, soft_flags=soft_flags,
        confidence=confidence, confidence_reason=confidence_reason, final_classification=final_classification,
        research_evidence=research_evidence, research_invoked=research, limitations=limitations,
    )

    return {"assessment": assessment, "profile": profile}


def _serialize(result: dict) -> dict:
    if "error" in result:
        return result
    return {"assessment": result["assessment"].model_dump(), "profile": result["profile"].model_dump()}


@tool
def assess_airport_opportunity_tool(airport_code: str, investment_focus: str = "general_modernization") -> dict:
    """Run a full Airport Modernization Opportunity Assessment for ONE
    airport: demand, passenger-side pressure, flight-side pressure, likely
    bottleneck, investment fit, actionability, confidence, and final
    classification. Use for "why is X a good/bad candidate" or "what's the
    unmet demand at X and why" type questions about a single airport."""
    return _serialize(assess_airport_opportunity(airport_code, investment_focus=investment_focus, research=False))
