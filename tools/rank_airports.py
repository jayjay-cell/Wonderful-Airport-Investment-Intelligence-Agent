"""rank_airports: resolves a region/state into candidate airports, screens
them cheaply by passenger volume (already returned by find_airports -- no
extra fetch), then computes opportunity_score() (core/scoring.py) for a
bounded shortlist and sorts by it.

The shortlist bound exists purely for cost: opportunity_score() needs a
full profile (BTS T-100 + On-Time, the two slow live sources) per
candidate, and a region can have 20+ airports. Screening by volume first
and only fully profiling a shortlist is a request-cost decision, not a
scoring decision -- it does not change how any individual airport is
scored, only how many get scored before this call returns.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from langchain_core.tools import tool

from core.metrics import latest_complete_year
from core.models import InvestmentFocus
from core.ranking import scope_label
from core.scoring import opportunity_score
from data.clients import bts_on_time, bts_t100
from tools.find_airports import find_airports
from tools.get_airport_profile import get_airport_profile

logger = logging.getLogger("tools.rank_airports")

_DEFAULT_SHORTLIST_SIZE = 8
_MAX_SHORTLIST_SIZE = 15
_PROFILE_CONCURRENCY = 8


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

    # Screen by passenger volume -- already have this from find_airports,
    # no extra fetch. Bigger airports are more likely to be genuinely
    # relevant investment candidates; this is a cost-bounding heuristic,
    # not part of the score itself.
    shortlist_airports = sorted(
        all_candidates, key=lambda a: enplanements_by_code.get(a.code, 0), reverse=True,
    )[:shortlist_size]

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

    def _profile(airport):
        return airport, get_airport_profile(airport.code, include_bts=True)

    with ThreadPoolExecutor(max_workers=min(_PROFILE_CONCURRENCY, len(shortlist_airports))) as pool:
        profile_results = list(pool.map(_profile, shortlist_airports))

    profiles, profile_errors = [], []
    for airport, result in profile_results:
        if "error" in result:
            profile_errors.append({"airport_code": airport.code, **result})
            continue
        profiles.append(result["profile"])

    if not profiles:
        return {"error": "INSUFFICIENT_DATA", "detail": "No shortlisted candidate could be profiled.", "errors": profile_errors}

    scored = sorted(
        ((p, opportunity_score(p, focus)) for p in profiles),
        key=lambda pair: pair[1].value if pair[1].value is not None else -1,
        reverse=True,
    )
    top = scored[:top_n]

    return {
        "investment_focus": focus.value,
        "candidate_set_description": discovery["candidate_set_description"],
        "candidate_count": len(all_candidates),
        "scored_count": len(profiles),
        "scope_label": scope_label(discovery["candidate_set_description"], is_national=False),
        "screening_note": (
            f"{len(all_candidates)} commercial-service airports match {discovery['candidate_set_description']}. "
            f"The top {len(shortlist_airports)} by passenger volume were fully profiled and scored. "
            f"This bounds response time for region-wide rankings -- a cost-scoping choice, not a claim "
            f"that unscreened airports lack opportunity. Separately, opportunity_score_coverage_ratio on "
            f"each candidate shows how much of that candidate's own scoring data was actually available -- "
            f"a top-ranked candidate with a low coverage_ratio rests on thinner data than one with full "
            f"coverage, even at a similar score."
        ),
        "ranked_candidates": [
            {
                "airport_code": p.airport.code, "airport_name": p.airport.name,
                "opportunity_score": score.value,
                "opportunity_score_basis": score.basis,
                "opportunity_score_missing": score.missing,
                "opportunity_score_coverage_ratio": score.coverage_ratio,
                "limitations": score.limitations,
            }
            for p, score in top
        ],
        "profile_errors": profile_errors,
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
      operations_technology, general_modernization) for an
      opportunity_score-based ranking -- "which airports are candidates for
      terminal expansion". Leave metric empty for this mode.

    For a SINGLE-airport investment question ("is SFO a good investment",
    "what's the unmet demand at SFO and why") do NOT call this tool --
    there is no dedicated single-airport scoring tool. Instead call
    get_airport_profile_tool (real numbers: growth, load factor, delay
    rate, congestion) and, if useful, research_airport_facts_tool
    (documented constraints, master plans), then explain the answer
    yourself from those two structured results.

    If the user's notion of "largest"/"best" is ambiguous (passengers vs.
    operations vs. physical area -- physical area has no data source), ask
    which they mean, or default to passenger volume and state that
    assumption.

    `state`, if used, must be the 2-letter USPS code (e.g. "CA", "TX") --
    convert a full state name yourself before calling."""
    if metric:
        from tools.rank_by_metric import SUPPORTED_METRICS, rank_airports_by_metric
        if metric not in SUPPORTED_METRICS:
            return {"error": "AMBIGUOUS_QUERY", "detail": f"Unsupported metric {metric!r}. Supported: {sorted(SUPPORTED_METRICS)}."}
        return rank_airports_by_metric(metric=metric, region=region or None, state=state or None, limit=top_n)

    focus = investment_focus or "general_modernization"
    return rank_airports(region=region or None, state=state or None, investment_focus=focus, top_n=top_n)
