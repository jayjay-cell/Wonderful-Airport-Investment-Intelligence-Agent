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
calls, even concurrently, cannot reliably stay fast. With the shared
resource-caching fix (data/clients/bts_on_time.py, bts_t100.py — one
national download per year/month regardless of how many airports ask), the
per-airport cost of Tier 2 is now dominated by parse time, not repeated
downloads — but a single BTS source can still take several seconds, so the
shortlist is intentionally kept small and bounded rather than relying on
concurrency alone. Default shortlist size is 3 (interactive-demo scale),
configurable via `shortlist_size`.

Tier 2 pre-warms the shared BTS resources for the WHOLE shortlist up front
(get_routes_bulk / get_on_time_records_bulk) before running individual
per-airport assessments — with single-flight caching this happens
automatically regardless of call order, but pre-warming makes the "one
shared download for the shortlist" property deterministic rather than
dependent on thread-scheduling luck.

Research Agent (top-3 preliminary candidates) is not yet wired in — see
module-level note in assess_opportunity.py; this is a documented gap, not
a silent omission.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from agent.tools.assess_opportunity import assess_airport_opportunity
from agent.tools.find_airports import find_airports
from core.models import InvestmentFocus, Level
from core.timing import timed
from core.ranking import rank_candidates, scope_label
from core.service_period import latest_complete_year
from data.clients import bts_on_time, bts_t100

logger = logging.getLogger("agent.tools.rank_airports")

_RESEARCH_TOP_N = 3
_SCREEN_CONCURRENCY = 10
_FULL_ASSESS_CONCURRENCY = 5
_DEFAULT_SHORTLIST_SIZE = 3  # interactive-demo scale, per explicit requirement
_MAX_SHORTLIST_SIZE = 5      # hard ceiling even if a caller requests more

_LEVEL_RANK = {Level.HIGH: 3, Level.MEDIUM: 2, Level.LOW: 1, Level.INSUFFICIENT: 0}


def rank_airports(
    region: str | None = None,
    state: str | None = None,
    investment_focus: str = "general_modernization",
    top_n: int = 10,
    shortlist_size: int = _DEFAULT_SHORTLIST_SIZE,
) -> dict:
    try:
        focus = InvestmentFocus(investment_focus)
    except ValueError:
        return {
            "error": "AMBIGUOUS_QUERY",
            "detail": f"Unrecognized investment_focus {investment_focus!r}.",
        }

    shortlist_size = max(1, min(shortlist_size, _MAX_SHORTLIST_SIZE))

    with timed("rank_airports.discover_candidates", region=region, state=state):
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

    with timed("rank_airports.screen_all_candidates", candidate_count=len(all_candidates)):
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
    ][:shortlist_size]

    # Pre-warm the shared BTS resources for the whole shortlist in one shot
    # before per-airport assessment. With single-flight caching this is not
    # strictly required for correctness (concurrent per-airport calls below
    # would still collapse into one download each), but it makes "one
    # shared national download for the shortlist" deterministic rather than
    # dependent on thread-scheduling timing, and surfaces a source failure
    # once up front instead of once per airport.
    year = latest_complete_year()
    shortlist_codes = [a.code for a in shortlist_airports]
    try:
        bts_t100.get_routes_bulk(shortlist_codes, year)
    except Exception as exc:
        logger.warning("rank_airports: T-100 pre-warm failed (%s) — per-airport calls will retry individually", exc)
    try:
        bts_on_time.get_on_time_records_bulk(shortlist_codes, year, 1)
    except Exception as exc:
        logger.warning("rank_airports: On-Time pre-warm failed (%s) — per-airport calls will retry individually", exc)

    # --- Tier 2: full assessment (FAA + BTS) on the shortlist only ---
    def _assess_full(airport):
        return airport, assess_airport_opportunity(
            airport.code, investment_focus=focus.value, research=False, include_bts=True,
        )

    with timed("rank_airports.full_assess_shortlist", shortlist_size=len(shortlist_airports)):
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

        def _val(metric):
            # Metric fields (p.passenger_cagr, p.passengers, ...) can
            # themselves be None (source unavailable), not just their
            # .value — guard both, rather than assuming a MetricValue is
            # always present.
            return (metric.value if metric is not None else None) or 0.0

        metrics_by_airport[airport.code] = {
            "passenger_cagr": _val(p.passenger_cagr),
            "passenger_volume": _val(p.passengers),
            "departure_delay_rate": _val(p.departure_delay_rate),
            "departure_cagr": 0.0,  # not yet separately computed
            "departure_volume": _val(p.departures),
            "growth": _val(p.passenger_cagr),
            "volume": _val(p.passengers),
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
