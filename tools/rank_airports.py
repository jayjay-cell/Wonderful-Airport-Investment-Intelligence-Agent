# ORIENTATION: TOOL. Multi-dimension opportunity ranking within a region/state. Two-tier: screen cheap, deep-assess a shortlist.
"""rank_airports: two-tier ranking to keep region-wide queries fast.

TIER 1 -- fast screen (FAA-only): every candidate airport in the requested
region/state gets a quick assessment using only FAA sources.
TIER 2 -- full assessment (FAA + BTS): only the top candidates from Tier 1
get the full assessment, including the two slow BTS sources.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from langchain_core.tools import tool

from core.metrics import latest_complete_year
from core.models import InvestmentFocus, Level
from core.ranking import rank_candidates, scope_label
from data.clients import bts_on_time, bts_t100
from tools.assess_opportunity import assess_airport_opportunity
from tools.find_airports import find_airports

logger = logging.getLogger("tools.rank_airports")

_RESEARCH_TOP_N = 3
_SCREEN_CONCURRENCY = 10
_FULL_ASSESS_CONCURRENCY = 5
_DEFAULT_SHORTLIST_SIZE = 3
_MAX_SHORTLIST_SIZE = 5

_LEVEL_RANK = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1, Level.INSUFFICIENT: 0}


def rank_airports(region: str | None = None, state: str | None = None, investment_focus: str = "general_modernization", top_n: int = 10, shortlist_size: int = _DEFAULT_SHORTLIST_SIZE) -> dict:
    try:
        focus = InvestmentFocus(investment_focus)
    except ValueError:
        return {"error": "AMBIGUOUS_QUERY", "detail": f"Unrecognized investment_focus {investment_focus!r}."}

    shortlist_size = max(1, min(shortlist_size, _MAX_SHORTLIST_SIZE))

    discovery = find_airports(region=region, state=state, commercial_only=True)
    if "error" in discovery:
        return discovery

    all_candidates = discovery["airports"]
    if not all_candidates:
        return {"error": "INSUFFICIENT_DATA", "detail": f"No commercial-service airports found for {discovery['candidate_set_description']}."}
    enplanements_by_code = discovery.get("enplanements_by_code", {})

    def _screen(airport):
        return airport, assess_airport_opportunity(airport.code, investment_focus=focus.value, research=False, include_bts=False)

    with ThreadPoolExecutor(max_workers=min(_SCREEN_CONCURRENCY, len(all_candidates))) as pool:
        screen_results = list(pool.map(_screen, all_candidates))

    screened, screen_errors = [], []
    for airport, result in screen_results:
        if "error" in result:
            screen_errors.append({"airport_code": airport.code, **result})
            continue
        screened.append((airport, result["assessment"].demand_level.level))

    if not screened:
        return {"error": "INSUFFICIENT_DATA", "detail": "No candidate airport could even be screened with FAA data.", "errors": screen_errors}

    shortlist_airports = [
        a for a, _ in sorted(screened, key=lambda pair: (_LEVEL_RANK[pair[1]], enplanements_by_code.get(pair[0].code, 0)), reverse=True)
    ][:shortlist_size]

    year = latest_complete_year()
    shortlist_codes = [a.code for a in shortlist_airports]
    try:
        bts_t100.get_routes_bulk(shortlist_codes, year)
    except Exception as exc:
        logger.warning("rank_airports: T-100 pre-warm failed (%s)", exc)
    try:
        bts_on_time.get_on_time_records_bulk(shortlist_codes, year)
    except Exception as exc:
        logger.warning("rank_airports: On-Time pre-warm failed (%s)", exc)

    def _assess_full(airport):
        return airport, assess_airport_opportunity(airport.code, investment_focus=focus.value, research=False, include_bts=True)

    with ThreadPoolExecutor(max_workers=min(_FULL_ASSESS_CONCURRENCY, len(shortlist_airports))) as pool:
        full_results = list(pool.map(_assess_full, shortlist_airports))

    assessments, assessment_errors, metrics_by_airport = [], [], {}

    for airport, result in full_results:
        if "error" in result:
            assessment_errors.append({"airport_code": airport.code, **result})
            continue

        assessment = result["assessment"]
        assessments.append(assessment)
        p = result["profile"]

        def _val(metric):
            return (metric.value if metric is not None else None) or 0.0

        metrics_by_airport[airport.code] = {
            "passenger_yoy_growth": _val(p.passenger_yoy_growth),
            "passenger_volume": _val(p.passengers),
            "departure_delay_rate": _val(p.departure_delay_rate),
            "departure_cagr": 0.0,
            "departure_volume": _val(p.departures),
            "growth": _val(p.passenger_yoy_growth),
            "volume": _val(p.passengers),
        }

    if not assessments:
        return {"error": "INSUFFICIENT_DATA", "detail": "No shortlisted candidate could be fully assessed.", "errors": assessment_errors}

    ranked = rank_candidates(assessments, focus, metrics_by_airport)
    top = ranked[:top_n]
    research_pending_for = [a.airport.code for a in ranked[:_RESEARCH_TOP_N]]

    return {
        "investment_focus": focus.value,
        "candidate_set_description": discovery["candidate_set_description"],
        "candidate_count": len(all_candidates),
        "screened_count": len(screened),
        "fully_assessed_count": len(assessments),
        "scope_label": scope_label(discovery["candidate_set_description"], is_national=False),
        "tiered_methodology_note": (
            f"{len(all_candidates)} commercial-service airports match {discovery['candidate_set_description']}. "
            f"All were screened using FAA demand data. The top {len(shortlist_airports)} by demand and "
            f"passenger volume then received a full assessment including live BTS operational data. "
            f"This keeps response time reasonable for region-wide rankings -- a scoping choice, not a "
            f"claim that unscreened airports lack opportunity."
        ),
        "ranked_candidates": [
            {
                "airport_code": a.airport.code, "airport_name": a.airport.name,
                "need_level": a.need_level.level.value, "demand_level": a.demand_level.level.value,
                "passenger_side_pressure": a.passenger_side_pressure.level.value,
                "flight_side_pressure": a.flight_side_pressure.level.value,
                "likely_bottleneck": a.likely_bottleneck.value, "investment_fit": a.investment_fit.value,
                "actionability": a.actionability.value, "final_classification": a.final_classification.value,
                "confidence": a.confidence.value, "confidence_reason": a.confidence_reason,
                "limitations": a.limitations,
            }
            for a in top
        ],
        "research_note": (
            f"Research was not invoked for the top {_RESEARCH_TOP_N} preliminary candidates "
            f"({research_pending_for}) -- Actionability and Investment Fit for these results are "
            f"based on structured data only and default toward Unknown/Likely rather than Confirmed."
        ),
        "screen_errors": screen_errors,
        "assessment_errors": assessment_errors,
    }


@tool
def rank_airports_tool(region: str = "", state: str = "", metric: str = "", investment_focus: str = "", top_n: int = 10) -> dict:
    """Rank airports -- either a multi-factor investment-opportunity ranking
    OR a single-metric ranking. ONE tool for both, so you never have to
    choose between two similarly-named ranking tools:

    - Pass `metric` (one of: enplanements, passenger_growth, operations,
      delay_rate, cancellation_rate) for a straightforward single-number
      ranking -- "which airport is largest by passengers", "which airports
      have the highest delay rate". Works nationally (omit region/state) or
      within a region/state. Leave investment_focus empty for this mode.

    - Pass `investment_focus` (one of: terminal, gates, runway_airfield,
      operations_technology, general_modernization) for a multi-factor
      modernization-candidate ranking -- "which airports are candidates for
      terminal expansion". This mode requires a region or state (it runs a
      full assessment per candidate, so it is not offered nationally).
      Leave metric empty for this mode.

    If the user's notion of "largest"/"best" is ambiguous (passengers vs.
    operations vs. physical area -- physical area has no data source), ask
    which they mean, or default to passenger volume and state that
    assumption."""
    if metric:
        from tools.rank_by_metric import SUPPORTED_METRICS, rank_airports_by_metric
        if metric not in SUPPORTED_METRICS:
            return {"error": "AMBIGUOUS_QUERY", "detail": f"Unsupported metric {metric!r}. Supported: {sorted(SUPPORTED_METRICS)}."}
        return rank_airports_by_metric(metric=metric, region=region or None, state=state or None, limit=top_n)

    focus = investment_focus or "general_modernization"
    return rank_airports(region=region or None, state=state or None, investment_focus=focus, top_n=top_n)
