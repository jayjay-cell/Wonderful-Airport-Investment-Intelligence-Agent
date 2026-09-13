"""rank_airports_by_metric tool: generic national/regional ranking by a
single deterministic metric. Fixes a real gap — "which airport is largest"
(by passengers), "which airport has the most annual passengers", etc. had
no tool to answer, so the agent could only refuse. This does NOT replace
rank_airports (which ranks by the multi-dimension opportunity methodology
for a specific investment focus) — it answers a simpler, single-metric
question.

Supported metrics split into two cost tiers:
- CHEAP metrics (enplanements, passenger_growth) are served entirely from
  the already-cached national FAA enplanements table — one shared download
  regardless of how many airports are ranked, genuinely fast for all ~2000
  commercial-service airports.
- EXPENSIVE metrics (operations, delay_rate, cancellation_rate, growth in
  operations) require BTS data per airport. For these, the same bounded-
  shortlist principle used in rank_airports.py applies: screen candidates
  cheaply first (by enplanements, as a scale proxy), then only pull BTS
  data for a small shortlist — never for the whole nation in one request.
"""

from __future__ import annotations

from core.service_period import latest_complete_year
from data.clients import bts_on_time, bts_t100, faa_enplanements

CHEAP_METRICS = {"enplanements", "passenger_growth"}
EXPENSIVE_METRICS = {"operations", "delay_rate", "cancellation_rate"}
SUPPORTED_METRICS = CHEAP_METRICS | EXPENSIVE_METRICS

_EXPENSIVE_SCREEN_LIMIT = 15  # how many candidates (by enplanements) get BTS data pulled
_EXPENSIVE_CONCURRENCY_NOTE = (
    "Expensive metrics are computed for a bounded shortlist of the largest "
    "candidates by passenger volume, not literally every US commercial "
    "airport, to keep response time reasonable."
)


def rank_airports_by_metric(
    metric: str,
    region: str | None = None,
    state: str | None = None,
    year: int | None = None,
    limit: int = 10,
) -> dict:
    """Ranks US commercial-service airports by one deterministic metric.

    metric: one of SUPPORTED_METRICS.
    region/state: optional scope narrowing (state code, e.g. "AK"). If both
        are omitted, ranks nationally.
    year: defaults to the latest complete year available.
    limit: max airports to return (results are still meaningful beyond
        the cheap/expensive boundary — see docstring).
    """
    if metric not in SUPPORTED_METRICS:
        return {
            "error": "AMBIGUOUS_QUERY",
            "detail": f"Unsupported metric {metric!r}. Supported metrics: "
                      f"{sorted(SUPPORTED_METRICS)}.",
        }

    limit = max(1, min(limit, 50))
    year = year or latest_complete_year()

    # region is accepted for interface symmetry with other tools, but the
    # underlying enplanements table is keyed by state, not named region —
    # resolve region to states via the same table used elsewhere.
    state_filter = state
    if region and not state:
        from data.regions import resolve_region
        states = resolve_region(region)
        if states is None:
            return {
                "error": "AMBIGUOUS_QUERY",
                "detail": f"{region!r} is not a recognized region. Try a "
                          f"specific state code instead.",
            }
        state_filter = None  # multi-state region; filter after fetch below
    else:
        states = [state_filter] if state_filter else None

    try:
        if states and len(states) > 1:
            all_records = []
            for s in states:
                all_records.extend(faa_enplanements.get_all_enplanements(state=s))
        else:
            all_records = faa_enplanements.get_all_enplanements(state=state_filter)
    except Exception as exc:
        return {
            "error": "SOURCE_UNAVAILABLE",
            "detail": f"FAA Commercial Service Enplanements unavailable: {exc}",
        }

    if not all_records:
        scope = region or state or "the United States"
        return {
            "error": "INSUFFICIENT_DATA",
            "detail": f"No commercial-service airport records found for {scope}.",
        }

    if metric in CHEAP_METRICS:
        return _rank_cheap(all_records, metric, region, state, year, limit)
    return _rank_expensive(all_records, metric, region, state, year, limit)


def _rank_cheap(records: list[dict], metric: str, region, state, year, limit) -> dict:
    def _sort_key(r):
        if metric == "enplanements":
            return r.get("enplanements_current_year") or 0
        # passenger_growth
        return r.get("pct_change") or 0.0

    ranked = sorted(records, key=_sort_key, reverse=True)[:limit]

    return {
        "metric": metric,
        "scope": region or state or "United States",
        "year": year,
        "source_name": "FAA Commercial Service Enplanements",
        "results": [
            {
                "airport_code": r["code"],
                "airport_name": r.get("name"),
                "state": r.get("state"),
                "value": r.get("enplanements_current_year") if metric == "enplanements" else r.get("pct_change"),
                "hub_class": r["hub_class"].value if hasattr(r.get("hub_class"), "value") else r.get("hub_class"),
            }
            for r in ranked
        ],
    }


def _rank_expensive(records: list[dict], metric: str, region, state, year, limit) -> dict:
    # Screen by enplanements first (scale proxy) — bounded shortlist, per
    # module docstring, rather than pulling BTS data for every airport.
    screened = sorted(records, key=lambda r: r.get("enplanements_current_year") or 0, reverse=True)
    shortlist = screened[:_EXPENSIVE_SCREEN_LIMIT]

    values = []
    errors = []
    for r in shortlist:
        code = r["code"]
        try:
            if metric == "operations":
                t100 = bts_t100.get_annual_routes(code, year)
                total_departures = sum(route["departures_performed"] for route in t100["routes"])
                values.append((code, r.get("name"), r.get("state"), total_departures))
            else:  # delay_rate or cancellation_rate
                ontime = bts_on_time.get_on_time_records(code, year, 1)
                value = ontime.get(metric)
                if value is not None:
                    values.append((code, r.get("name"), r.get("state"), value))
        except Exception as exc:
            errors.append({"airport_code": code, "detail": str(exc)})
            continue

    values.sort(key=lambda t: t[3], reverse=True)
    ranked = values[:limit]

    return {
        "metric": metric,
        "scope": region or state or "United States",
        "year": year,
        "source_name": "BTS T-100 Segment" if metric == "operations" else "BTS Reporting Carrier On-Time Performance",
        "methodology_note": (
            f"{_EXPENSIVE_CONCURRENCY_NOTE} Screened the top "
            f"{len(shortlist)} candidates by passenger volume; ranked "
            f"{len(values)} that returned usable data."
        ),
        "results": [
            {"airport_code": code, "airport_name": name, "state": state, "value": value}
            for code, name, state, value in ranked
        ],
        "errors": errors,
    }
