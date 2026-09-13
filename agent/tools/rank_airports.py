"""rank_airports tool: two-tier ranking to keep region-wide queries fast.

TIER 1 — fast screen (FAA-only, no BTS): every candidate airport in the
requested region/state gets a quick assessment using only FAA sources
(identity, enplanements, TAF), which are consistently sub-second. This
gives Demand Level for every candidate at low cost.

TIER 2 — full assessment (FAA + BTS): only the top candidates from Tier 1
(by Demand Level, then passenger volume) get the full assessment, including
the two slow BTS sources (T-100 form scrape, On-Time download) that carry
the actual pressure/bottleneck signals.

WHY THIS SHAPE: assessing every commercial airport in a region with live BTS
calls, even concurrently, cannot reliably stay fast — BTS alone has shown
20-90s per airport under load in testing, and one run of the old
all-candidates design did not complete even after 5 minutes and the server
process did not survive it. Splitting into a cheap FAA-only screen (seconds,
for up to ~25 candidates) followed by a small, bounded full-assessment tier
(the actual slow part, but now applied to ~5-8 airports instead of 20+)
targets a ~20-30s total budget for a full-region ranking, which is the
realistic latency target for this data shape — not the 5-10s of a pure
lookup, but not the 5+ minutes of the naive all-candidates design either.

Research Agent (top-3 preliminary candidates) is not yet wired in — see
module-level note in assess_opportunity.py; this is a documented gap, not
a silent omission.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent.tools.assess_opportunity import assess_airport_opportunity
from agent.tools.find_airports import find_airports
from core.models import InvestmentFocus, Level
from core.ranking import rank_candidates, scope_label

_RESEARCH_TOP_N = 3
_SCREEN_CONCURRENCY = 10
_FULL_ASSESS_CONCURRENCY = 5
_MAX_FULLY_ASSESSED = 8  # Tier 2 shortlist size — see module docstring

_LEVEL_RANK = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1, Level.INSUFFICIENT: 0}


def rank_airports(
    region: str | None = None,
    state: str | None = None,
    investment_focus: str = "general_modernization",
    top_n: int = 10,
) -> dict:
    try:
        focus = InvestmentFocus(investment_focus)
    except ValueError:
        return {
            "error": "AMBIGUOUS_QUERY",
            "detail": f"Unrecognized investment_focus {investment_focus!r}.",
        }

    discovery = find_airports(region=region, state=state, commercial_only=True)
    if "error" in discovery:
        return discovery

    all_candidates = discovery["airports"]
    if not all_candidates:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": f"No commercial-service airports found for "
                      f"{discovery['candidate_set_description']}.",
        }
    enplanements_by_code = discovery.get("enplanements_by_code", {})

    # --- Tier 1: fast FAA-only screen across every candidate ---
    def _screen(airport):
        return airport, assess_airport_opportunity(
            airport.code, investment_focus=focus.value, research=False, include_bts=False,
        )

    with ThreadPoolExecutor(max_workers=min(_SCREEN_CONCURRENCY, len(all_candidates))) as pool:
        screen_results = list(pool.map(_screen, all_candidates))

    screened = []
    screen_errors = []
    for airport, result in screen_results:
        if "error" in result:
            screen_errors.append({"airport_code": airport.code, **result})
            continue
        screened.append((airport, result["assessment"].demand_level.level))

    if not screened:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": "No candidate airport could even be screened with FAA data.",
            "errors": screen_errors,
        }

    # Shortlist for Tier 2: highest Demand Level first, then passenger volume.
    shortlist_airports = [
        a for a, _ in sorted(
            screened,
            key=lambda pair: (_LEVEL_RANK[pair[1]], enplanements_by_code.get(pair[0].code, 0)),
            reverse=True,
        )
    ][:_MAX_FULLY_ASSESSED]

    # --- Tier 2: full assessment (FAA + BTS) on the shortlist only ---
    def _assess_full(airport):
        return airport, assess_airport_opportunity(
            airport.code, investment_focus=focus.value, research=False, include_bts=True,
        )

    with ThreadPoolExecutor(max_workers=min(_FULL_ASSESS_CONCURRENCY, len(shortlist_airports))) as pool:
        full_results = list(pool.map(_assess_full, shortlist_airports))

    assessments = []
    assessment_errors = []
    metrics_by_airport = {}

    for airport, result in full_results:
        if "error" in result:
            assessment_errors.append({"airport_code": airport.code, **result})
            continue

        assessment = result["assessment"]
        assessments.append(assessment)

        p = result["profile"]
        metrics_by_airport[airport.code] = {
            "passenger_cagr": p.passenger_cagr.value or 0.0,
            "passenger_volume": p.passengers.value or 0.0,
            "departure_delay_rate": p.departure_delay_rate.value or 0.0,
            "departure_cagr": 0.0,  # not yet separately computed
            "departure_volume": p.departures.value or 0.0,
            "growth": p.passenger_cagr.value or 0.0,
            "volume": p.passengers.value or 0.0,
        }

    if not assessments:
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": "No shortlisted candidate could be fully assessed.",
            "errors": assessment_errors,
        }

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
            f"{len(all_candidates)} commercial-service airports match "
            f"{discovery['candidate_set_description']}. All were screened "
            f"using FAA demand data (fast, no live delay/capacity data). The "
            f"top {len(shortlist_airports)} by demand and passenger volume "
            f"then received a full assessment including live BTS "
            f"operational data (delay rates, load factor, bottleneck "
            f"signals). This two-tier approach keeps response time "
            f"reasonable for region-wide rankings — it is a scoping choice, "
            f"not a claim that unscreened airports lack opportunity. Ask "
            f"about a specific airport directly for its full assessment."
        ),
        "ranked_candidates": [
            {
                "airport_code": a.airport.code,
                "airport_name": a.airport.name,
                "need_level": a.need_level.level.value,
                "demand_level": a.demand_level.level.value,
                "passenger_side_pressure": a.passenger_side_pressure.level.value,
                "flight_side_pressure": a.flight_side_pressure.level.value,
                "likely_bottleneck": a.likely_bottleneck.value,
                "investment_fit": a.investment_fit.value,
                "actionability": a.actionability.value,
                "final_classification": a.final_classification.value,
                "confidence": a.confidence.value,
                "confidence_reason": a.confidence_reason,
                "limitations": a.limitations,
            }
            for a in top
        ],
        "research_note": (
            f"Research Agent has not yet been invoked for the top "
            f"{_RESEARCH_TOP_N} preliminary candidates "
            f"({research_pending_for}) — Actionability and Investment Fit "
            f"for these results are based on structured data only and "
            f"default toward Unknown/Likely rather than Confirmed."
        ),
        "screen_errors": screen_errors,
        "assessment_errors": assessment_errors,
    }
