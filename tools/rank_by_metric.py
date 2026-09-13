# ORIENTATION: TOOL. National/regional ranking by ONE metric (e.g. "largest airport by passengers"). Not the opportunity ranking.
"""rank_airports_by_metric: generic national/regional ranking by a single
deterministic metric. Answers "which airport is largest" (by passengers),
"which airport has the most annual passengers", etc. Does NOT replace
rank_airports (multi-dimension opportunity methodology) -- this answers a
simpler, single-metric question, including nationally with no region/state.
"""

from __future__ import annotations

from core.metrics import latest_complete_year
from data.clients import bts_on_time, bts_t100, faa_enplanements
from tools.find_airports import is_national_scope

CHEAP_METRICS = {"enplanements", "passenger_growth"}
EXPENSIVE_METRICS = {"operations", "delay_rate", "cancellation_rate"}
SUPPORTED_METRICS = CHEAP_METRICS | EXPENSIVE_METRICS

_EXPENSIVE_SCREEN_LIMIT = 15


def rank_airports_by_metric(metric: str, region: str | None = None, state: str | None = None, year: int | None = None, limit: int = 10) -> dict:
    if metric not in SUPPORTED_METRICS:
        return {"error": "AMBIGUOUS_QUERY", "detail": f"Unsupported metric {metric!r}. Supported metrics: {sorted(SUPPORTED_METRICS)}."}

    limit = max(1, min(limit, 50))
    year = year or latest_complete_year()

    state_filter = None
    if region and not state:
        if is_national_scope(region):
            # "United States" etc. means NO filter, not an unrecognized
            # region -- a national single-metric ranking is the normal case
            # for "which airport is largest in the US".
            states = None
        else:
            from data.regions import resolve_region
            states = resolve_region(region)
            if states is None:
                return {"error": "AMBIGUOUS_QUERY", "detail": f"{region!r} is not a recognized region. Try a specific state code instead."}
    elif state:
        # Same expectation as find_airports_tool: `state` must be the
        # 2-letter USPS code. The model converts a full name itself (see
        # rank_airports_tool's docstring) -- no static lookup table here.
        code = state.strip().upper()
        if len(code) != 2 or not code.isalpha():
            return {
                "error": "AMBIGUOUS_QUERY",
                "detail": f"{state!r} is not a 2-letter US state code. Convert it to "
                          f"the standard USPS code first (e.g. California -> CA).",
            }
        state_filter = code
        states = [code]
    else:
        states = None

    try:
        if states and len(states) > 1:
            all_records = []
            for s in states:
                all_records.extend(faa_enplanements.get_all_enplanements(state=s))
        else:
            all_records = faa_enplanements.get_all_enplanements(state=state_filter)
    except Exception as exc:
        return {"error": "SOURCE_UNAVAILABLE", "detail": f"FAA Commercial Service Enplanements unavailable: {exc}"}

    if not all_records:
        scope = region or state or "the United States"
        return {"error": "INSUFFICIENT_DATA", "detail": f"No commercial-service airport records found for {scope}."}

    if metric in CHEAP_METRICS:
        return _rank_cheap(all_records, metric, region, state, year, limit)
    return _rank_expensive(all_records, metric, region, state, year, limit)


def _rank_cheap(records: list[dict], metric: str, region, state, year, limit) -> dict:
    def _sort_key(r):
        return (r.get("enplanements_current_year") or 0) if metric == "enplanements" else (r.get("pct_change") or 0.0)

    ranked = sorted(records, key=_sort_key, reverse=True)[:limit]
    return {
        "metric": metric, "scope": region or state or "United States", "year": year,
        "source_name": "FAA Commercial Service Enplanements",
        "results": [
            {
                "airport_code": r["code"], "airport_name": r.get("name"), "state": r.get("state"),
                "value": r.get("enplanements_current_year") if metric == "enplanements" else r.get("pct_change"),
                "hub_class": r["hub_class"].value if hasattr(r.get("hub_class"), "value") else r.get("hub_class"),
            }
            for r in ranked
        ],
    }


def _rank_expensive(records: list[dict], metric: str, region, state, year, limit) -> dict:
    screened = sorted(records, key=lambda r: r.get("enplanements_current_year") or 0, reverse=True)
    shortlist = screened[:_EXPENSIVE_SCREEN_LIMIT]

    values, errors = [], []
    for r in shortlist:
        code = r["code"]
        try:
            if metric == "operations":
                t100 = bts_t100.get_annual_routes(code, year)
                total_departures = sum(route["departures_performed"] for route in t100["routes"])
                values.append((code, r.get("name"), r.get("state"), total_departures))
            else:
                ontime = bts_on_time.get_on_time_records(code, year)
                value = ontime.get(metric)
                if value is not None:
                    values.append((code, r.get("name"), r.get("state"), value))
        except Exception as exc:
            errors.append({"airport_code": code, "detail": str(exc)})
            continue

    values.sort(key=lambda t: t[3], reverse=True)
    ranked = values[:limit]

    return {
        "metric": metric, "scope": region or state or "United States", "year": year,
        "source_name": "BTS T-100 Segment" if metric == "operations" else "BTS Reporting Carrier On-Time Performance",
        "methodology_note": (
            f"Expensive metrics are computed for a bounded shortlist of the largest candidates "
            f"by passenger volume, not every US commercial airport, to keep response time reasonable. "
            f"Screened the top {len(shortlist)} candidates by passenger volume; ranked {len(values)} "
            f"that returned usable data."
        ),
        "results": [{"airport_code": code, "airport_name": name, "state": state, "value": value} for code, name, state, value in ranked],
        "errors": errors,
    }


# No standalone @tool here -- this is an internal helper the model never
# calls directly. rank_airports_tool (in tools/rank_airports.py) is the
# ONE ranking entry point the model sees; it dispatches here when the plan
# names a single metric. Merged deliberately: two separate tools both named
# "rank_airports_*" was the exact class of confusion that caused a real
# tool-selection bug elsewhere (compare_airports vs. get_airport_profile) --
# see agent/graph.py's system prompt for the reasoning.
